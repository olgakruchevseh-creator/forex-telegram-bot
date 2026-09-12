"""CRT (Candle Range Theory) с подтверждением и конfluence AMD / Power of Three.

Логика не пытается давать направление на каждом скане: используется только
закрытый H1 range, подтверждённый sweep одной границы, возврат внутрь и
последующий displacement через 50% диапазона. M15/H4 и сила валют фильтруют
ложные развороты. Совпавший завершённый AMD повышает качество, но не является
обязательным: CRT остаётся самостоятельным модулем.
"""
from __future__ import annotations
from chart_snapshot import freeze_by_tf
import hashlib, io, json, logging, os
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair
import amd_power_of_three

log = logging.getLogger("fxbot.crt")
TF_MINUTES = {"H4": 240, "H1": 60, "M15": 15}
_PENDING_CARDS: dict[str, tuple[dict, dict]] = {}


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "crt_candle_range_state.json"

def _load() -> dict:
    try:
        d = json.loads(_path().read_text()); return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError, OSError): return {}

def _save(d: dict) -> None:
    p = _path(); p.parent.mkdir(parents=True, exist_ok=True); t = p.with_suffix(".tmp")
    t.write_text(json.dumps(d, ensure_ascii=False, indent=2)); t.replace(p)

def _bias(tf: str, bars: list[Candle]) -> int:
    v = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return v.bias if v else 0

def _strength(symbol: str, side: str, strength: dict[str, float]) -> tuple[bool, float]:
    base, quote = split_pair(symbol); gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "CRT_MIN_STRENGTH_GAP", .04))
    return (gap >= need if side == "LONG" else gap <= -need), gap

def detect_crt(symbol: str, h1: list[Candle], h4: list[Candle], m15: list[Candle], strength: dict[str, float]) -> dict | None:
    if len(h1) < 22 or min(len(h4), len(m15)) < 20: return None
    av = atr(h1, 14)
    if av <= 0: return None
    lookback = int(getattr(cfg, "CRT_REFERENCE_LOOKBACK_H1", 8))
    min_range = av * float(getattr(cfg, "CRT_MIN_RANGE_ATR", .70))
    max_range = av * float(getattr(cfg, "CRT_MAX_RANGE_ATR", 2.80))
    sweep_need = av * float(getattr(cfg, "CRT_MIN_SWEEP_ATR", .06))
    confirm_buffer = av * float(getattr(cfg, "CRT_MID_CONFIRM_ATR", .05))
    body_need = av * float(getattr(cfg, "CRT_MIN_CONFIRM_BODY_ATR", .22))
    max_sweep_age = int(getattr(cfg, "CRT_MAX_SWEEP_AGE_H1", 3))
    candidates = []
    start = max(0, len(h1) - lookback - max_sweep_age - 2)
    for r in range(start, len(h1)-2):
        ref = h1[r]; width = ref.high-ref.low
        if not (min_range <= width <= max_range): continue
        mid = (ref.high+ref.low)/2
        for s in range(r+1, min(len(h1)-1, r+1+max_sweep_age)):
            sweep = h1[s]
            low_sweep = sweep.low < ref.low-sweep_need and sweep.close > ref.low
            high_sweep = sweep.high > ref.high+sweep_need and sweep.close < ref.high
            if low_sweep == high_sweep: continue
            side = "LONG" if low_sweep else "SHORT"; wanted = 1 if side == "LONG" else -1
            # Только подтверждение ПОСЛЕ sweep; никогда не используем незакрытую свечу.
            confirmations = h1[s+1:]
            if side == "LONG":
                confirmations = [c for c in confirmations if c.close > mid+confirm_buffer and c.close > c.open and abs(c.close-c.open) >= body_need]
            else:
                confirmations = [c for c in confirmations if c.close < mid-confirm_buffer and c.close < c.open and abs(c.close-c.open) >= body_need]
            if not confirmations: continue
            confirm = confirmations[0]
            # Сигнал должен быть свежим: подтверждение — последняя закрытая H1 или
            # максимум одна H1 назад. Иначе это уже историческая картинка.
            confirm_i = next((i for i,c in enumerate(h1) if c.dt == confirm.dt), -99)
            age = len(h1)-1-confirm_i
            if age > int(getattr(cfg, "CRT_MAX_CONFIRM_AGE_H1", 1)): continue
            if _bias("H4", h4) == -wanted or _bias("M15", m15) == -wanted: continue
            ok, gap = _strength(symbol, side, strength)
            if not ok: continue
            amd = amd_power_of_three.detect_amd(symbol, h1, h4, m15, strength)
            amd_match = bool(amd and amd.get("side") == side and not amd.get("late"))
            sweep_depth = (ref.low-sweep.low if side == "LONG" else sweep.high-ref.high)/av
            quality = 76 + min(7, int(max(0, sweep_depth)*10))
            quality += 4 if _bias("M15", m15) == wanted else 0
            quality += 3 if _bias("H4", h4) == wanted else 0
            quality += min(4, int(abs(gap)*25))
            quality += 6 if amd_match else 0
            quality = min(96, quality)
            candidates.append({
                "key": f"{symbol}|{side}|{ref.dt}|{sweep.dt}|{confirm.dt}", "symbol": symbol, "side": side,
                "ref_dt": ref.dt, "sweep_dt": sweep.dt, "confirm_dt": confirm.dt,
                "low": ref.low, "high": ref.high, "mid": mid,
                "sweep_price": sweep.low if side == "LONG" else sweep.high,
                "confirm_price": confirm.close, "target": ref.high if side == "LONG" else ref.low,
                "gap": gap, "quality": quality, "confidence": min(93, quality-4), "amd_match": amd_match,
            })
    return max(candidates, key=lambda e: (e["amd_match"], e["quality"], e["confirm_dt"]), default=None)

def _p(symbol: str, v: float) -> str: return f"{v:.3f}" if "JPY" in symbol else f"{v:.5f}"

def format_message(e: dict) -> str:
    icon = "🟢" if e["side"] == "LONG" else "🔴"
    swept = "LOW" if e["side"] == "LONG" else "HIGH"
    amd = "ПОДТВЕРЖДЕНО — AMD совпадает" if e["amd_match"] else "CRT подтверждён самостоятельно"
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", f"🕯 CRT — CANDLE RANGE THEORY · {e['side']}", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {e['symbol']}", "📊 Рабочий диапазон: H1", f"Направление: {e['side']} {icon}",
        f"📦 CRT Range: {_p(e['symbol'],e['low'])}–{_p(e['symbol'],e['high'])}",
        f"50% диапазона: {_p(e['symbol'],e['mid'])}", f"🧹 Манипуляция: снят {swept} до {_p(e['symbol'],e['sweep_price'])}",
        f"✅ Подтверждение: возврат + displacement через 50% · {_p(e['symbol'],e['confirm_price'])}",
        f"🎯 Противоположная граница CRT: {_p(e['symbol'],e['target'])}", f"🔗 Связка AMD: {amd}",
        f"Разница силы валют: {e['gap']:+.2f}", f"Качество: {e['quality']}/100", f"Вероятность: {e['confidence']}%", "",
        "Факт: CRT отправлен только после закрытого sweep, возврата в диапазон и подтверждённого движения через середину диапазона."
    ])

def render_chart(e: dict, by_tf: dict) -> io.BytesIO:
    from PIL import Image, ImageDraw, ImageFont
    bars = closed_candles(by_tf.get("H1") or [], 60)[-48:]
    W,H=1200,720; im=Image.new("RGB",(W,H),"#10131d"); d=ImageDraw.Draw(im,"RGBA")
    try:
        f=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",22); s=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",16)
    except OSError: f=s=ImageFont.load_default()
    L,R,T,B=72,1135,88,610; vals=[v for c in bars for v in (c.low,c.high)]+[e["low"],e["high"],e["sweep_price"],e["confirm_price"]]
    lo,hi=min(vals),max(vals); pad=max((hi-lo)*.08,1e-6); lo-=pad; hi+=pad
    x=lambda i:L+i/max(1,len(bars)-1)*(R-L); y=lambda p:B-(p-lo)/max(1e-12,hi-lo)*(B-T)
    for n in range(6): d.line((L,T+n*(B-T)/5,R,T+n*(B-T)/5),fill="#293040",width=1)
    d.rectangle((L,y(e["high"]),R,y(e["low"])),fill="#4aa3ff18",outline="#4aa3ff",width=2)
    d.line((L,y(e["mid"]),R,y(e["mid"])),fill="#f4dc4b",width=2)
    cw=max(4,int((R-L)/max(1,len(bars))*.55)); idx={c.dt:i for i,c in enumerate(bars)}
    for i,c in enumerate(bars):
        xx=x(i); col="#37d67a" if c.close>=c.open else "#ff5c6c"; d.line((xx,y(c.high),xx,y(c.low)),fill=col,width=2)
        a,b=y(c.open),y(c.close); d.rectangle((xx-cw/2,min(a,b),xx+cw/2,max(a,b)+1),fill=col)
    si=idx.get(e["sweep_dt"],max(0,len(bars)-2)); ci=idx.get(e["confirm_dt"],len(bars)-1); col="#42e889" if e["side"]=="LONG" else "#ff6575"
    d.ellipse((x(si)-9,y(e["sweep_price"])-9,x(si)+9,y(e["sweep_price"])+9),fill="#ff6575",outline="#ffffff",width=2)
    d.line((x(si),y(e["sweep_price"]),x(ci),y(e["confirm_price"])),fill=col,width=5)
    d.text((L,26),f"{e['symbol']} · CRT · {e['side']} · AMD {'✓' if e['amd_match'] else '—'}",fill="#f1f5fb",font=f)
    d.text((L,650),"Закрытые H1-свечи · синяя зона CRT · жёлтая линия 50%",fill="#aeb7c6",font=s)
    out=io.BytesIO(); out.name=f"crt_{e['symbol'].replace('/','')}_{e['side']}.png"; im.save(out,"PNG",optimize=True); out.seek(0); return out

def image_for_alert(text: str) -> io.BytesIO | None:
    card=_PENDING_CARDS.get(text)
    return render_chart(card[0],card[1]) if card and getattr(cfg,"CRT_CHART_IMAGES_ENABLED",True) else None

def process_market(market: dict, strength: dict[str,float]) -> list[str]:
    _PENDING_CARDS.clear(); state=_load(); sent=state.setdefault("sent",{}); pending=state.setdefault("pending",{}); first=not state.get("bootstrapped"); msgs=[]
    for digest,item in list(pending.items()):
        e=item.get("event") if isinstance(item,dict) else None
        if not e or e.get("key") in sent: pending.pop(digest,None); continue
        text=format_message(e); msgs.append(text); _PENDING_CARDS[text]=(e,freeze_by_tf(market.get(e["symbol"]) or {}))
    pkeys={v.get("key") for v in pending.values() if isinstance(v,dict)}
    for symbol in cfg.PAIRS:
        try:
            by=market.get(symbol) or {}; h1=closed_candles(by.get("H1") or [],60); h4=closed_candles(by.get("H4") or [],240); m15=closed_candles(by.get("M15") or [],15)
            e=detect_crt(symbol,h1,h4,m15,strength)
            if not e or e["key"] in sent or e["key"] in pkeys: continue
            text=format_message(e); digest=hashlib.sha256(text.encode()).hexdigest()[:20]
            if first: sent[e["key"]]=e["key"]
            else: pending[digest]={"key":e["key"],"event":e}; msgs.append(text); _PENDING_CARDS[text]=(e,freeze_by_tf(by)); pkeys.add(e["key"])
        except Exception: log.exception("CRT %s",symbol)
    state["bootstrapped"]=True
    if len(sent)>600: state["sent"]=dict(list(sent.items())[-450:])
    if len(pending)>50: state["pending"]=dict(list(pending.items())[-50:])
    _save(state); return msgs

def mark_delivered(text: str) -> bool:
    state=_load(); digest=hashlib.sha256((text or "").encode()).hexdigest()[:20]; item=(state.get("pending") or {}).get(digest)
    if not item: return False
    state.setdefault("sent",{})[item["key"]]=item["key"]; state["pending"].pop(digest,None); _save(state); _PENDING_CARDS.pop(text,None); return True
