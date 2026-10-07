"""Smart Money 62-26 — строгий SMC-конфлюэнс для закрытых свечей.

Название 62-26 сохранено как имя модуля. Сигнал не строится по одному
индикатору: нужны HTF-направление, снятие ликвидности, MSS/BOS, displacement,
FVG/OB и возврат в рабочую retracement-зону. Незакрытые свечи не используются.
"""
from __future__ import annotations
from chart_snapshot import freeze_by_tf

import io
import json
import hashlib
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
import ohlc_movement
import pullback_regime
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.smart_money_62_26")
TF_MINUTES = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
_LAST_CHART_CARDS: dict[str, tuple[dict, dict]] = {}


@dataclass
class Setup:
    setup_id: str
    symbol: str
    side: str
    sweep_level: float
    sweep_price: float
    mss_level: float
    impulse_low: float
    impulse_high: float
    zone_low: float
    zone_high: float
    fvg_low: float
    fvg_high: float
    created_dt: str
    quality: int
    age: int = 0
    sweep_dt: str = ""
    displacement_dt: str = ""
    last_dt: str = ""
    sent: bool = False
    invalid: bool = False
    temporal_anchor_dt: str = ""


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "smart_money_62_26_state.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(data: dict) -> None:
    dest = _path(); dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _bars(by_tf: dict, tf: str) -> list[Candle]:
    return closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])


def _bias(tf: str, bars: list[Candle]) -> int:
    if len(bars) < 25:
        return 0
    view = analyze_tf(tf, tf, bars)
    return view.bias if view else 0


def _pivots(bars: list[Candle], n: int = 2) -> list[tuple[int, float, str]]:
    out = []
    for i in range(n, len(bars)-n):
        area = bars[i-n:i+n+1]
        if bars[i].high >= max(c.high for c in area): out.append((i, bars[i].high, "high"))
        if bars[i].low <= min(c.low for c in area): out.append((i, bars[i].low, "low"))
    return out[-30:]


def _strength_ok(symbol: str, side: str, strength: dict[str, float]) -> tuple[bool, float]:
    base, quote = split_pair(symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "SMC_62_26_MIN_STRENGTH_GAP", .05))
    return (gap >= need if side == "LONG" else gap <= -need), gap


def _find_setup(symbol: str, d1: list[Candle], h4: list[Candle], h1: list[Candle], m15: list[Candle]) -> Setup | None:
    if min(len(h4), len(h1), len(m15)) < 35:
        return None
    htf = _bias("H4", h4)
    if htf == 0:
        return None
    av = atr(h1, 14)
    if av <= 0:
        return None
    # Ищем sweep + market-structure shift только в последних закрытых H1.
    piv = _pivots(h1[:-2], 2)
    highs = [(i,p) for i,p,k in piv if k == "high"]
    lows = [(i,p) for i,p,k in piv if k == "low"]
    if not highs or not lows:
        return None
    sweep_buf = av * float(getattr(cfg, "SMC_62_26_SWEEP_ATR", .06))
    bos_buf = av * float(getattr(cfg, "SMC_62_26_BOS_ATR", .05))
    body_need = av * float(getattr(cfg, "SMC_62_26_DISPLACEMENT_ATR", .65))
    max_age = int(getattr(cfg, "SMC_62_26_SETUP_LOOKBACK_H1", 6))

    for j in range(len(h1)-2, max(2, len(h1)-2-max_age), -1):
        sweep = h1[j]
        before = h1[max(0,j-12):j]
        if len(before) < 6: continue
        prior_high = max(c.high for c in before)
        prior_low = min(c.low for c in before)
        # Sweep opposite the intended direction.
        if htf == 1 and sweep.low < prior_low-sweep_buf and sweep.close > prior_low:
            side, sweep_level, sweep_price = "LONG", prior_low, sweep.low
            mss_level = max(c.high for c in h1[max(0,j-6):j])
            temporal_anchor = max(before, key=lambda x: x.high).dt
        elif htf == -1 and sweep.high > prior_high+sweep_buf and sweep.close < prior_high:
            side, sweep_level, sweep_price = "SHORT", prior_high, sweep.high
            mss_level = min(c.low for c in h1[max(0,j-6):j])
            temporal_anchor = min(before, key=lambda x: x.low).dt
        else:
            continue
        # После sweep обязателен закрытый displacement через MSS/BOS.
        for k in range(j+1, len(h1)):
            c = h1[k]
            body = abs(c.close-c.open)
            broke = c.close > mss_level+bos_buf if side == "LONG" else c.close < mss_level-bos_buf
            impulse = c.close > c.open if side == "LONG" else c.close < c.open
            if not (broke and impulse and body >= body_need):
                continue
            impulse_low = min(x.low for x in h1[j:k+1])
            impulse_high = max(x.high for x in h1[j:k+1])
            rng = impulse_high-impulse_low
            if rng < av * 1.1: continue
            # 62-26 is a price/time cluster, not a 26%-wide price band.
            # Price geometry is centred on the canonical 61.8% retracement;
            # tolerance is volatility-normalised and deliberately narrow.
            retr = float(getattr(cfg, "SMC_62_26_RETRACE", .618))
            if side == "LONG":
                anchor = impulse_high-rng*retr
            else:
                anchor = impulse_low+rng*retr
            half = min(rng * float(getattr(cfg, "SMC_62_26_MAX_ZONE_RATIO", .08)),
                       av * float(getattr(cfg, "SMC_62_26_ZONE_ATR", .18)))
            zone_low, zone_high = anchor-half, anchor+half
            # Трёхсвечный FVG рядом с displacement повышает качество.
            fvg_low=fvg_high=0.0
            if k >= 2:
                if side == "LONG" and h1[k-2].high < c.low:
                    fvg_low, fvg_high = h1[k-2].high, c.low
                elif side == "SHORT" and h1[k-2].low > c.high:
                    fvg_low, fvg_high = c.high, h1[k-2].low
            quality = 72 + (8 if fvg_high > fvg_low else 0) + (6 if _bias("D1", d1) == htf else 0)
            setup_id = f"{symbol}|{side}|{sweep.dt}|{c.dt}|{mss_level:.6f}"
            return Setup(setup_id, symbol, side, sweep_level, sweep_price, mss_level,
                         impulse_low, impulse_high, zone_low, zone_high, fvg_low, fvg_high,
                         c.dt, min(90, quality), last_dt=c.dt, sweep_dt=sweep.dt, displacement_dt=c.dt,
                         temporal_anchor_dt=temporal_anchor)
    return None


def _bars_between(m15: list[Candle], start_dt: str, end_dt: str) -> int:
    if not start_dt or not end_dt or end_dt < start_dt:
        return 0
    return max(0, len([c for c in m15 if start_dt <= c.dt <= end_dt]) - 1)


def _time_geometry(s: Setup, m15: list[Candle], confirm_dt: str) -> dict:
    """Measure the 62->26 relationship as two separate closed-bar legs.

    The reference leg runs from the pre-sweep structural anchor to the sweep.
    The correction leg runs from displacement/BOS to the closed H1 reaction.
    Timing is confluence only: it never manufactures direction or bypasses H1.
    """
    major = _bars_between(m15, s.temporal_anchor_dt, s.sweep_dt)
    correction = _bars_between(m15, s.displacement_dt, confirm_dt)
    target_major = int(getattr(cfg, "SMC_62_26_TIME_MAJOR_BARS", 62))
    target_corr = int(getattr(cfg, "SMC_62_26_TIME_CORRECTION_BARS", 26))
    tol_major = int(getattr(cfg, "SMC_62_26_TIME_MAJOR_TOLERANCE", 10))
    tol_corr = int(getattr(cfg, "SMC_62_26_TIME_CORRECTION_TOLERANCE", 5))
    if major <= 0 or correction <= 0:
        return {"major": major, "correction": correction, "cluster": "NONE", "ratio_error": 1.0}
    expected = target_corr / max(1, target_major)
    actual = correction / major
    ratio_error = abs(actual - expected) / expected
    major_ok = abs(major-target_major) <= tol_major
    corr_ok = abs(correction-target_corr) <= tol_corr
    ratio_ok = ratio_error <= float(getattr(cfg, "SMC_62_26_TIME_RATIO_ERROR", .22))
    cluster = "62→26" if major_ok and corr_ok and ratio_ok else ("RATIO" if ratio_ok else "NONE")
    return {"major": major, "correction": correction, "cluster": cluster, "ratio_error": ratio_error}


def _confirm(s: Setup, by_tf: dict, d1: list[Candle], h4: list[Candle], h1: list[Candle], m15: list[Candle], m5: list[Candle], strength: dict[str,float]) -> dict | None:
    # Main confirmation is CLOSED H1. M15/M5 are auxiliary only.
    if s.sent or s.invalid or len(h1) < 25 or len(m15) < 25 or len(m5) < 25:
        return None
    c = h1[-1]
    if c.dt <= s.created_dt or c.dt == s.last_dt:
        return None
    s.last_dt = c.dt; s.age += 1
    av = atr(h1,14)
    if av <= 0:
        return None
    if s.age > int(getattr(cfg,"SMC_62_26_MAX_AGE_H1",8)):
        s.invalid=True; return None
    invalid = av * float(getattr(cfg,"SMC_62_26_INVALIDATION_ATR",.12))
    wanted = 1 if s.side == "LONG" else -1
    if s.side == "LONG":
        if c.close < s.sweep_price-invalid: s.invalid=True; return None
        touched = c.low <= s.zone_high and c.high >= s.zone_low
        held = c.close > s.zone_low and c.close > c.open
    else:
        if c.close > s.sweep_price+invalid: s.invalid=True; return None
        touched = c.high >= s.zone_low and c.low <= s.zone_high
        held = c.close < s.zone_high and c.close < c.open
    if not (touched and held):
        return None

    h4b, d1b = _bias("H4",h4), _bias("D1",d1)
    if h4b != wanted or d1b == -wanted:
        return None

    # Shared project classifier owns pullback-vs-flat geometry. A 1-2 candle
    # touch or a RANGE/COMPRESSION is not promoted to a 62-26 pullback.
    pb = pullback_regime.classify(s.symbol, -wanted, d1b, h4b, by_tf)
    if pb.mode != "PULLBACK":
        return None

    # LTF may confirm timing, but can never create the signal.
    m15b, m5b = _bias("M15",m15), _bias("M5",m5)
    if m15b == -wanted and m5b == -wanted:
        return None
    ok,gap = _strength_ok(s.symbol,s.side,strength)
    if not ok:
        return None

    early = ohlc_movement.early_entry_check(by_tf, wanted)
    if not early.get("allow", True):
        return None
    og = ohlc_movement.guard_event(by_tf, wanted, s.quality)
    if not og.get("allow", True):
        return None

    tg = _time_geometry(s, m15, c.dt)
    time_cluster = tg["cluster"]
    time_bonus = 5 if time_cluster == "62→26" else (2 if time_cluster == "RATIO" else 0)
    ltf_bonus = 3 if (m15b == wanted or m5b == wanted) else 0
    quality=min(97,s.quality+6+ltf_bonus+time_bonus+min(5,int(abs(gap)*30))+int(og.get("quality_delta",0)))
    return {**asdict(s),"close":c.close,"gap":gap,"quality":quality,"confidence":min(93,quality-3),
            "confirm_dt":c.dt,"pullback_mode":pb.mode,"pullback_bars":pb.bars,
            "pullback_move_atr":pb.move_atr,"pullback_efficiency":pb.efficiency,
            "market_regime":pb.regime,"time_major_bars_m15":tg["major"],
            "time_correction_bars_m15":tg["correction"],"time_cluster":time_cluster,
            "time_ratio_error":tg["ratio_error"],"early_reason":early.get("reason","")}


def _price(symbol: str, v: float) -> str:
    return f"{v:.3f}" if "JPY" in symbol else f"{v:.5f}"


def format_message(e: dict) -> str:
    fvg = (f"{_price(e['symbol'],e['fvg_low'])}–{_price(e['symbol'],e['fvg_high'])}" if e['fvg_high']>e['fvg_low'] else "нет обязательного FVG")
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━","🏦 SMART MONEY 62-26 — СИГНАЛ ПОДТВЕРЖДЁН","━━━━━━━━━━━━━━━━━━","",
        f"💱 Пара: {e['symbol']}",f"Направление: {e['side']}",
        f"Снятая ликвидность: {_price(e['symbol'],e['sweep_level'])}",
        f"Экстремум sweep: {_price(e['symbol'],e['sweep_price'])}",
        f"MSS / BOS: {_price(e['symbol'],e['mss_level'])}",
        f"Зона 61.8%: {_price(e['symbol'],e['zone_low'])}–{_price(e['symbol'],e['zone_high'])}",
        f"FVG: {fvg}",f"Цена подтверждения H1: {_price(e['symbol'],e['close'])}",
        f"Откат: {e.get('pullback_mode','—')} · {e.get('pullback_bars',0)} H1 · {e.get('pullback_move_atr',0):.2f} ATR · эффективность {e.get('pullback_efficiency',0):.0%}",
        f"Временная геометрия: {e.get('time_major_bars_m15',0)}→{e.get('time_correction_bars_m15',0)} M15 · кластер {e.get('time_cluster','NONE')} · ошибка ratio {e.get('time_ratio_error',1.0):.0%}",
        "Подтверждение: H4 направление · H1 sweep+MSS/displacement · H1 реакция · M15/M5 только вспомогательно",
        f"Разница силы валют: {e['gap']:+.2f}",f"Качество: {e['quality']}/100",f"Вероятность: {e['confidence']}%","",
        f"✅ Факт: ликвидность снята, структура сменилась в {e['side']}, цена дала многосвечный откат к 61.8% и реакцию на закрытой H1; 62→26 измеряется как две временные ноги и остаётся только конфлюэнцией.",
    ])


def process_market(market: dict, strength: dict[str,float]) -> list[str]:
    state=_load(); first=not bool(state.get("bootstrapped"))
    setups={k:Setup(**v) for k,v in (state.get("setups") or {}).items()}; pending=state.setdefault("pending",{}); messages=[item["text"] for item in pending.values() if isinstance(item,dict) and item.get("text")]
    for symbol in cfg.PAIRS:
        try:
            by_tf=market.get(symbol) or {}
            d1,h4,h1,m15,m5=(_bars(by_tf,tf) for tf in ("D1","H4","H1","M15","M5"))
            for s in list(setups.values()):
                if s.symbol!=symbol or s.sent or s.invalid: continue
                e=_confirm(s,by_tf,d1,h4,h1,m15,m5,strength)
                if e and not first:
                    text=format_message(e); digest=hashlib.sha256(text.encode()).hexdigest()[:20]; pending[digest]={"setup_id":s.setup_id,"text":text}; messages.append(text); _LAST_CHART_CARDS[text]=(e,freeze_by_tf(by_tf))
            fresh=_find_setup(symbol,d1,h4,h1,m15)
            if fresh and fresh.setup_id not in setups:
                # Новый setup заменяет старые незавершённые того же направления.
                for old in setups.values():
                    if old.symbol==symbol and old.side==fresh.side and not old.sent: old.invalid=True
                setups[fresh.setup_id]=fresh
        except Exception:
            log.exception("Smart Money 62-26 %s",symbol)
    state["bootstrapped"]=True
    kept=[s for s in setups.values() if not s.invalid]
    state["setups"]={s.setup_id:asdict(s) for s in kept[-400:]}; _save(state)
    return messages


def mark_delivered(text: str) -> bool:
    state=_load(); digest=hashlib.sha256((text or "").encode()).hexdigest()[:20]; item=(state.get("pending") or {}).pop(digest,None)
    if not item: return False
    rec=(state.get("setups") or {}).get(item.get("setup_id"))
    if rec: rec["sent"]=True
    _save(state); _LAST_CHART_CARDS.pop(text,None); return True

def render_chart(event: dict, by_tf: dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    bars=_bars(by_tf,"H1")[-72:]
    if not bars: return None
    fig,ax=plt.subplots(figsize=(11,5.5))
    xs=list(range(len(bars)))
    for i,c in enumerate(bars):
        ax.vlines(i,c.low,c.high,linewidth=.8)
        lo=min(c.open,c.close); height=max(abs(c.close-c.open),1e-9)
        ax.add_patch(plt.Rectangle((i-.3,lo),.6,height,fill=False,linewidth=1))
    ax.axhspan(event["zone_low"],event["zone_high"],alpha=.15)
    ax.axhline(event["sweep_level"],linestyle="--",linewidth=1,label="Liquidity")
    ax.axhline(event["mss_level"],linestyle=":",linewidth=1,label="MSS/BOS")
    ax.set_title(f"{event['symbol']} · Smart Money 62-26 · {event['side']} · H1")
    ax.legend(loc="best"); ax.grid(alpha=.15); fig.tight_layout()
    buf=io.BytesIO(); fig.savefig(buf,format="png",dpi=150); plt.close(fig); buf.seek(0); return buf


def image_for_alert(text: str):
    card=_LAST_CHART_CARDS.get(text)
    return render_chart(*card) if card else None
