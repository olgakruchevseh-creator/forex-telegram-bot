"""POC / price-acceptance profile for spot FX.

Twelve Data OHLC used by this bot has no centralized exchange volume. Therefore
this module deliberately computes a TPO/price-acceptance POC proxy (time spent
at price), never labels it as exchange Volume POC. Alerts are event-driven and
only emitted after a closed-candle reaction/reclaim/rejection around the POC.
"""
from __future__ import annotations
from chart_snapshot import freeze_by_tf

import io
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config as cfg
import module_evidence_bus
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.poc")
TF_MINUTES = {"H4": 240, "H1": 60, "M15": 15}
_LAST_CHART_CARDS: dict[str, tuple[dict, dict]] = {}
_LAST_KEYS: dict[str, str] = {}


def _state_path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "poc_state.json"


def _load_keys() -> dict[str, str]:
    global _LAST_KEYS
    if _LAST_KEYS:
        return _LAST_KEYS
    try:
        data = json.loads(_state_path().read_text())
        if isinstance(data, dict):
            _LAST_KEYS = {str(k): str(v) for k, v in data.items()}
    except (FileNotFoundError, ValueError, OSError):
        _LAST_KEYS = {}
    return _LAST_KEYS


def _save_keys() -> None:
    dest = _state_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(_LAST_KEYS, ensure_ascii=False, indent=2))
    tmp.replace(dest)


@dataclass
class Profile:
    poc: float
    val: float
    vah: float
    step: float
    bins: list[float]
    weights: list[float]
    vwap: float | None = None
    vwap_source: str = "UNAVAILABLE"
    hvns: tuple[float, ...] = ()
    lvns: tuple[float, ...] = ()
    shape: str = "BALANCED"


def _bars(by_tf: dict, tf: str) -> list[Candle]:
    return closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])


def _profile(bars: list[Candle], bins_n: int = 48, value_area: float = .70) -> Profile | None:
    """Build a TPO-style acceptance profile from closed OHLC ranges."""
    if len(bars) < 30:
        return None
    lo = min(c.low for c in bars)
    hi = max(c.high for c in bars)
    if hi <= lo:
        return None
    bins_n = max(24, min(96, int(bins_n)))
    step = (hi-lo)/bins_n
    centers = [lo + (i+.5)*step for i in range(bins_n)]
    w = [0.0]*bins_n
    # Each candle contributes one unit of time across prices it traded through;
    # body prices receive a small acceptance premium. No fake volume is created.
    for c in bars:
        a = max(0, min(bins_n-1, int((c.low-lo)/step)))
        b = max(0, min(bins_n-1, int((c.high-lo)/step)))
        body_lo, body_hi = sorted((c.open, c.close))
        span = max(1, b-a+1)
        for i in range(a, b+1):
            x = centers[i]
            w[i] += 1.0/span
            if body_lo <= x <= body_hi:
                w[i] += .35/span
    p = max(range(bins_n), key=lambda i: w[i])
    total = sum(w)
    target = total * max(.50, min(.90, value_area))
    chosen = {p}; acc = w[p]; left, right = p-1, p+1
    while acc < target and (left >= 0 or right < bins_n):
        lw = w[left] if left >= 0 else -1
        rw = w[right] if right < bins_n else -1
        if rw >= lw:
            chosen.add(right); acc += rw; right += 1
        else:
            chosen.add(left); acc += lw; left -= 1
    # True volume-weighted average only when the provider really supplied volume.
    # For physical FX Twelve Data normally does not, so vwap remains None.
    vb = [(c, float(c.volume)) for c in bars if getattr(c, "volume", None) is not None and float(c.volume) > 0]
    vwap = None
    source = "UNAVAILABLE"
    if len(vb) >= max(10, int(len(bars) * .80)):
        den = sum(v for _, v in vb)
        if den > 0:
            vwap = sum(((c.high + c.low + c.close) / 3.0) * v for c, v in vb) / den
            source = "PROVIDER_VOLUME"
    return _enrich_profile(Profile(centers[p], min(centers[i] for i in chosen)-step/2,
                   max(centers[i] for i in chosen)+step/2, step, centers, w, vwap, source))



def _profile_nodes(prof: Profile) -> tuple[tuple[float, ...], tuple[float, ...], str]:
    """Local acceptance extrema. Deterministic; no future bars and no fake volume."""
    w = prof.weights
    if len(w) < 5 or not any(w):
        return (), (), "BALANCED"
    peak = max(w)
    hvn = [prof.bins[i] for i in range(1, len(w)-1)
           if w[i] >= w[i-1] and w[i] >= w[i+1] and w[i] >= peak*.55]
    positive = sorted(x for x in w if x > 0)
    floor = positive[max(0, int(len(positive)*.30)-1)] if positive else 0
    lvn = [prof.bins[i] for i in range(1, len(w)-1)
           if w[i] <= w[i-1] and w[i] <= w[i+1] and 0 < w[i] <= floor]
    # Keep only the strongest/most distinct nodes to avoid turning profile noise into evidence.
    hvn = sorted(hvn, key=lambda x: abs(x-prof.poc))[:4]
    lvn = sorted(lvn, key=lambda x: abs(x-prof.poc))[:4]
    lower = sum(x for b,x in zip(prof.bins,w) if b < prof.poc)
    upper = sum(x for b,x in zip(prof.bins,w) if b > prof.poc)
    den = max(lower+upper, 1e-12)
    skew = (upper-lower)/den
    shape = "TOP_HEAVY" if skew > .18 else "BOTTOM_HEAVY" if skew < -.18 else "BALANCED"
    return tuple(hvn), tuple(lvn), shape


def _enrich_profile(prof: Profile) -> Profile:
    hvn, lvn, shape = _profile_nodes(prof)
    prof.hvns, prof.lvns, prof.shape = hvn, lvn, shape
    return prof


def _composite_profile(h1: list[Candle]) -> Profile | None:
    n = int(getattr(cfg, "POC_COMPOSITE_LOOKBACK_H1", 120))
    if len(h1) < max(48, n//2):
        return None
    p = _profile(h1[-n:], int(getattr(cfg, "POC_PROFILE_BINS", 48)), float(getattr(cfg, "POC_VALUE_AREA", .70)))
    return _enrich_profile(p) if p else None


def _initial_balance(h1: list[Candle]) -> tuple[float, float] | None:
    """Daily H1 initial balance using the first closed bars of the current UTC day.

    This is price-range context, not volume. If timestamps are synthetic/unparseable,
    IB is unavailable rather than guessed from a rolling slice.
    """
    if not h1:
        return None
    try:
        parsed=[(datetime.strptime(c.dt[:19], "%Y-%m-%d %H:%M:%S"), c) for c in h1]
    except (TypeError, ValueError):
        return None
    day=parsed[-1][0].date()
    today=[c for dt,c in parsed if dt.date()==day]
    n=max(1, int(getattr(cfg, "POC_INITIAL_BALANCE_H1_BARS", 2)))
    if len(today) < n:
        return None
    seed=today[:n]
    return min(c.low for c in seed), max(c.high for c in seed)


def _balanced_target(prof: Profile, side: str) -> float:
    """Symmetric measured objective around POC/Value Area; context target only."""
    return prof.poc + (prof.poc-prof.val) if side == "LONG" else prof.poc - (prof.vah-prof.poc)


def _naked_poc(h1: list[Candle], cur_close: float, av: float) -> dict | None:
    """Find a completed daily-profile POC not revisited by later closed H1 bars."""
    chunk=max(12, int(getattr(cfg, "POC_NAKED_PROFILE_H1", 24)))
    tol=max(av*float(getattr(cfg, "POC_NAKED_TOUCH_ATR", .10)), 1e-12)
    max_profiles=max(1, int(getattr(cfg, "POC_NAKED_MAX_PROFILES", 5)))
    if len(h1) < chunk*2:
        return None
    candidates=[]
    # Completed chunks only; newest unfinished/current chunk is intentionally excluded.
    end=len(h1)-chunk
    for stop in range(end, max(chunk-1, end-chunk*max_profiles), -chunk):
        start=stop-chunk
        if start < 0: break
        p=_profile(h1[start:stop], int(getattr(cfg,"POC_PROFILE_BINS",48)), float(getattr(cfg,"POC_VALUE_AREA",.70)))
        if not p: continue
        later=h1[stop:]
        tapped=any(c.low <= p.poc+tol and c.high >= p.poc-tol for c in later)
        if not tapped:
            candidates.append((abs(cur_close-p.poc), p.poc, h1[stop-1].dt))
    if not candidates:
        return None
    _, price, completed_at=min(candidates)
    return {"price": price, "completed_at": completed_at, "state": "NAKED"}

def _bias(tf: str, bars: list[Candle]) -> int:
    v = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return v.bias if v else 0


def _strength_ok(symbol: str, side: str, strength: dict[str, float]) -> tuple[bool, float]:
    base, quote = split_pair(symbol)
    gap = strength.get(base, 0.0)-strength.get(quote, 0.0)
    need = float(getattr(cfg, "POC_MIN_STRENGTH_GAP", .04))
    return (gap >= need if side == "LONG" else gap <= -need), gap


def _value_lifecycle(symbol: str, by_tf: dict, prof: Profile, confirm_bars: list[Candle], av: float) -> dict:
    """Classify value interaction without inventing direction from a touch.

    CALCULATED -> APPROACH -> FIRST_TOUCH -> RETEST -> ACCEPTANCE/REJECTION ->
    STRUCTURE_CONFIRMED. Direction exists only after closed-candle reaction and
    structure agreement.
    """
    cur = confirm_bars[-1]
    refs = [("POC", prof.poc)]
    if prof.vwap is not None:
        refs.append(("VWAP", prof.vwap))
    name, ref = min(refs, key=lambda x: abs(cur.close-x[1]))
    dist = abs(cur.close-ref) / max(av, 1e-12)
    tol = max(prof.step*1.25, av*float(getattr(cfg, "POC_TOUCH_ATR", .12)))
    touched_now = cur.low <= ref+tol and cur.high >= ref-tol
    prior = confirm_bars[-int(getattr(cfg, "VALUE_RETEST_LOOKBACK", 6))-1:-1]
    prior_touches = sum(1 for c in prior if c.low <= ref+tol and c.high >= ref-tol)
    retest_ready = prior_touches >= 1
    state = "CALCULATED"
    if dist <= float(getattr(cfg, "VALUE_APPROACH_ATR", .30)): state = "APPROACH"
    if touched_now: state = "FIRST_TOUCH" if prior_touches == 0 else "RETEST"
    side = 0
    # First touch is observation only.  Acceptance/rejection may become directional
    # only on a later interaction (retest), unless the conservative guard is
    # explicitly disabled in config.
    require_retest = bool(getattr(cfg, "VALUE_REQUIRE_RETEST", True))
    reaction_ready = touched_now and (retest_ready or not require_retest)
    if reaction_ready and cur.close > ref+tol and cur.close > cur.open:
        state, side = "ACCEPTANCE", 1
    elif reaction_ready and cur.close < ref-tol and cur.close < cur.open:
        state, side = "REJECTION", -1
    structure_ok = False
    structure_state = ""
    if side:
        try:
            import structure_context
            sc = structure_context.analyze_symbol(symbol, by_tf, side)
            structure_state = sc.state
            structure_ok = bool(sc.side == side and sc.state in {"CONFIRMED", "SHIFT_CONFIRMED"})
        except Exception:
            log.exception("VALUE_STRUCTURE_CONTEXT_FAILED symbol=%s", symbol)
    if side and structure_ok:
        state = "STRUCTURE_CONFIRMED"
    return {"state": state, "reference": name, "reference_price": ref,
            "side": side, "structure_confirmed": structure_ok,
            "structure_state": structure_state, "distance_atr": dist,
            "prior_touches": prior_touches, "retest_ready": retest_ready,
            "vwap_available": prof.vwap is not None, "vwap_source": prof.vwap_source}


def detect(symbol: str, by_tf: dict, strength: dict[str, float]) -> dict | None:
    h4, h1, m15 = (_bars(by_tf, tf) for tf in ("H4", "H1", "M15"))
    lookback = int(getattr(cfg, "POC_LOOKBACK_H1", 72))
    if len(h1) < max(35, lookback//2) or len(m15) < 20 or len(h4) < 20:
        return None
    prof = _profile(h1[-lookback:], int(getattr(cfg, "POC_PROFILE_BINS", 48)),
                    float(getattr(cfg, "POC_VALUE_AREA", .70)))
    if not prof:
        return None
    # Main event is confirmed only by the last CLOSED H1. M15 remains auxiliary context.
    prev, cur = h1[-2], h1[-1]
    av = atr(h1, 14)
    if av <= 0:
        return None
    lifecycle = _value_lifecycle(symbol, by_tf, prof, h1, av)
    # Final confirmation must use the SAME value reference selected by the lifecycle.
    # When real provider volume exists this may be VWAP; otherwise it is the TPO POC.
    # A touch by itself never creates direction.
    ref = float(lifecycle.get("reference_price", prof.poc))
    ref_name = str(lifecycle.get("reference", "POC"))
    tol = max(prof.step*1.25, av*float(getattr(cfg, "POC_TOUCH_ATR", .12)))
    body = abs(cur.close-cur.open)
    min_body = av*float(getattr(cfg, "POC_CONFIRM_BODY_ATR", .22))
    touched = cur.low <= ref+tol and cur.high >= ref-tol
    retest_ok = bool(lifecycle.get("retest_ready")) or not bool(getattr(cfg, "VALUE_REQUIRE_RETEST", True))
    accept_long = retest_ok and touched and prev.close <= ref+tol and cur.close > ref+tol and cur.close > cur.open
    reject_short = retest_ok and touched and prev.close >= ref-tol and cur.close < ref-tol and cur.close < cur.open
    if body < min_body or not (accept_long or reject_short):
        return None
    side = "LONG" if accept_long else "SHORT"
    wanted = 1 if side == "LONG" else -1
    # A value touch/reclaim is context, never a standalone direction.
    if getattr(cfg, "VALUE_REQUIRE_STRUCTURE_CONFIRM", True):
        if lifecycle.get("side") != wanted or not lifecycle.get("structure_confirmed"):
            return None
    # VWAP/POC confirms acceptance/rejection; it does not override opposite H4 context.
    if _bias("H4", h4) == -wanted or _bias("H1", h1) != wanted:
        return None
    # M15 is confirmation only: explicit opposite bias vetoes, neutral does not create direction.
    if _bias("M15", m15) == -wanted:
        return None
    ok, gap = _strength_ok(symbol, side, strength)
    if not ok:
        return None
    distance = abs(cur.close-ref)/av
    if distance > float(getattr(cfg, "POC_MAX_ENTRY_DISTANCE_ATR", .65)):
        return None
    # Real VWAP close to POC is a bounded confluence only; never another vote.
    value_confluence = False
    if prof.vwap is not None:
        value_confluence = abs(prof.vwap-prof.poc)/av <= float(getattr(cfg, "VALUE_POC_VWAP_CONFLUENCE_ATR", .20))
    quality = 72 + min(8, int(body/av*5)) + min(6, int(abs(gap)*25)) + (3 if value_confluence else 0)
    if _bias("H1", h1) == wanted:
        quality += 5
    # Geometry/context extensions: bounded evidence, never independent direction votes.
    composite = _composite_profile(h1)
    ib = _initial_balance(h1)
    naked = _naked_poc(h1, cur.close, av)
    target = _balanced_target(prof, side)
    near_hvn = min((abs(cur.close-x) for x in prof.hvns), default=999.0) / max(av,1e-12) <= float(getattr(cfg,"POC_NODE_NEAR_ATR",.18))
    if near_hvn: quality += 2
    quality = min(94, quality)
    return {"symbol": symbol, "side": side, "poc": prof.poc, "val": prof.val, "vah": prof.vah,
            "close": cur.close, "dt": cur.dt, "gap": gap, "quality": quality, "tf": "H1",
            "profile_shape": prof.shape, "hvns": prof.hvns, "lvns": prof.lvns,
            "initial_balance": ib, "balanced_target": target, "naked_poc": naked,
            "composite_poc": composite.poc if composite else None,
            "value_reference": ref_name, "value_reference_price": ref,
            "value_confluence": value_confluence,
            "confidence": max(60, min(90, quality-4)), "profile": prof, "lifecycle": lifecycle}


def _price(symbol: str, v: float) -> str:
    return f"{v:.3f}" if "JPY" in symbol else f"{v:.5f}"


def format_message(e: dict) -> str:
    module_evidence_bus.publish('POC', e)
    ref_name = e.get("value_reference", "POC")
    action = (f"возврат и закрепление выше {ref_name}" if e["side"] == "LONG"
              else f"отбой и закрепление ниже {ref_name}")
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", f"🎯 POC — {e['side']}", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {e['symbol']}", f"Направление: {e['side']}",
        "Профиль: H1 · закрытые свечи", f"POC: {_price(e['symbol'], e['poc'])}",
        f"Value Area: {_price(e['symbol'], e['val'])}–{_price(e['symbol'], e['vah'])}",
        f"Закрытие H1: {_price(e['symbol'], e['close'])}",
        f"Опорная value-точка: {ref_name} · {_price(e['symbol'], e.get('value_reference_price', e['poc']))}",
        f"Реакция: {action}",
        f"Состояние value lifecycle: {e.get('lifecycle', {}).get('state', '—')}",
        f"Ретест подтверждён: {'да' if e.get('lifecycle', {}).get('retest_ready') else 'нет'}",
        f"Форма acceptance-профиля: {e.get('profile_shape', 'BALANCED')}",
        f"Composite POC: {_price(e['symbol'], e['composite_poc']) if e.get('composite_poc') is not None else '—'}",
        f"Naked POC: {_price(e['symbol'], e['naked_poc']['price']) if e.get('naked_poc') else '—'}",
        f"Balanced target: {_price(e['symbol'], e['balanced_target'])}",
        f"VWAP + POC конфлюэнс: {'да' if e.get('value_confluence') else 'нет/недоступен'}",
        (f"VWAP: {_price(e['symbol'], e['profile'].vwap)} · источник: фактический volume провайдера"
         if e['profile'].vwap is not None else "VWAP: недоступен для текущих FX-свечей · поддельный volume не создаётся"),
        f"Разница силы валют: {e['gap']:+.2f}", f"Качество: {e['quality']}/100",
        f"Вероятность: {e['confidence']}%", "",
        f"✅ Факт: закрытая H1 подтвердила реакцию у {ref_name}; структура подтверждена, H4 не противоречит направлению.",
        "ℹ️ POC рассчитан как TPO/price-acceptance proxy по OHLC, а не как биржевой Volume POC.",
    ])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    out = []
    keys = _load_keys()
    dirty = False
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            e = detect(symbol, by_tf, strength)
            if not e:
                continue
            key = f"{e['side']}|{e['dt']}|{e['poc']:.6f}"
            if keys.get(symbol) == key:
                continue
            keys[symbol] = key
            dirty = True
            text = format_message(e)
            out.append(text)
            _LAST_CHART_CARDS[text] = (e, freeze_by_tf(by_tf))
        except Exception:
            log.exception("POC %s", symbol)
    if dirty:
        try:
            _save_keys()
        except Exception:
            log.exception("POC_STATE_SAVE_FAILED")
    return out


def render_chart(e: dict, by_tf: dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    bars = _bars(by_tf, "H1")[-int(getattr(cfg, "POC_CHART_LOOKBACK", 72)): ]
    if not bars:
        return None
    fig, ax = plt.subplots(figsize=(10, 5.4))
    for i, c in enumerate(bars):
        ax.vlines(i, c.low, c.high, linewidth=1)
        bottom = min(c.open, c.close); height = max(abs(c.close-c.open), (c.high-c.low)*.015, 1e-8)
        ax.add_patch(Rectangle((i-.32, bottom), .64, height, fill=False, linewidth=1.1))
    ax.axhspan(e["val"], e["vah"], alpha=.08)
    ax.axhline(e["poc"], linestyle="--", linewidth=1.4)
    ax.text(len(bars)-1, e["poc"], " POC", ha="right", va="bottom")
    if e["profile"].vwap is not None:
        ax.axhline(e["profile"].vwap, linestyle=":", linewidth=1.2)
        ax.text(len(bars)-1, e["profile"].vwap, " VWAP", ha="right", va="top")
    ax.annotate(e["side"], (len(bars)-1, e["close"]), xytext=(-45, 20 if e["side"]=="LONG" else -28),
                textcoords="offset points", arrowprops={"arrowstyle":"->"})
    ax.set_title(f"{e['symbol']} · POC · {e['side']} · H1")
    ax.set_ylabel("Price"); ax.set_xlabel("Closed candles"); ax.grid(True, alpha=.2); fig.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=150, bbox_inches="tight"); plt.close(fig); buf.seek(0)
    return buf


def image_for_alert(text: str):
    if not getattr(cfg, "POC_CHART_IMAGES_ENABLED", True):
        return None
    card = _LAST_CHART_CARDS.pop(text, None)
    return render_chart(card[0], card[1]) if card else None
