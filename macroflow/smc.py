"""Smart Money Concepts — port dari "Smart Money Concepts [LuxAlgo]" (Pine v5, CC BY-NC-SA 4.0 © LuxAlgo).

Mengikuti logika script aslinya dengan setelan default:
- Struktur *internal* (leg 5) dan *swing* (leg `swing`, default 50), masing-masing punya pivot,
  arah tren, dan BOS/CHoCH sendiri (fungsi leg(), getCurrentStructure(), displayStructure()).
  Struktur internal hanya dihitung bila levelnya berbeda dari level swing (extraCondition).
- Internal Order Block dibuat saat struktur internal ditembus: candle dengan parsed low terendah
  (bullish) / parsed high tertinggi (bearish) antara pivot dan candle penembus. Candle volatil
  (high − low ≥ 2 × ATR200) high/low-nya ditukar (filter "Atr"). Swing OB tidak aktif (default LuxAlgo).
- Alert OB = *breakout / mitigasi* (setelan "High/Low"): low < batas bawah OB bullish
  (harga jebol demand → SELL), high > batas atas OB bearish (harga jebol supply → BUY).
- Fair Value Gap dengan "Auto Threshold" (timeframe chart).
Tidak repaint: semua kejadian di candle j hanya memakai data sampai j.
Input: OHLC candle yang dipakai indikator (Heikin Ashi bila aktif, sama dengan chart).
"""
from dataclasses import dataclass, field

import numpy as np

from . import indicators as ta

BULLISH, BEARISH = 1, -1
BULLISH_LEG, BEARISH_LEG = 1, 0
MAX_STORED_OB = 100


@dataclass
class SMC:
    choch_buy: np.ndarray
    choch_sell: np.ndarray
    bos_buy: np.ndarray
    bos_sell: np.ndarray
    ob_buy: np.ndarray       # OB bearish (supply) ditembus ke atas
    ob_sell: np.ndarray      # OB bullish (demand) ditembus ke bawah
    fvg_buy: np.ndarray
    fvg_sell: np.ndarray
    zones: dict = field(default_factory=dict)    # (nama, arah, j) -> (bawah, atas)
    levels: dict = field(default_factory=dict)   # (nama, arah, j) -> "internal" / "swing" / "swing+internal"


class _Pivot:
    def __init__(self):
        self.current = np.nan
        self.last = np.nan
        self.crossed = False
        self.index = 0


class _Leg:
    """leg(size) di Pine: 0 = bearish leg, 1 = bullish leg (state `var leg`)."""

    def __init__(self, size):
        self.size = size
        self.value = 0
        self.prev = None

    def update(self, h, l, j):
        prev = self.value
        s = self.size
        if j >= s:
            hh = h[j - s + 1:j + 1].max()   # ta.highest(size)
            ll = l[j - s + 1:j + 1].min()   # ta.lowest(size)
            if h[j - s] > hh:
                self.value = BEARISH_LEG
            elif l[j - s] < ll:
                self.value = BULLISH_LEG
        change = None if self.prev is None else self.value - prev
        self.prev = prev
        return change


def _structure(leg, hi_piv, lo_piv, h, l, j):
    """getCurrentStructure(): perbarui pivot saat leg baru dimulai."""
    ch = leg.update(h, l, j)
    if not ch:
        return
    s = leg.size
    p, lvl = (lo_piv, l[j - s]) if ch == 1 else (hi_piv, h[j - s])  # +1 = awal leg bullish = pivot low
    p.last, p.current, p.crossed, p.index = p.current, lvl, False, j - s


class _Cross:
    """ta.crossover / ta.crossunder dengan histori per call-site."""

    def __init__(self, up):
        self.up = up
        self.prev = None   # (close, level) bar sebelumnya

    def __call__(self, close, level):
        prev, self.prev = self.prev, (close, level)
        if prev is None or np.isnan(level) or np.isnan(prev[1]):
            return False
        if self.up:
            return close > level and prev[0] <= prev[1]
        return close < level and prev[0] >= prev[1]


def detect(o, h, l, c, swing=50, internal=5):
    n = len(c)
    z = lambda: np.zeros(n, bool)
    r = SMC(z(), z(), z(), z(), z(), z(), z(), z())
    if n < internal + 3:
        return r

    atr200 = ta.atr(h, l, c, 200)
    with np.errstate(invalid="ignore"):
        high_vol = (h - l) >= 2 * atr200            # NaN -> False (seperti na di Pine)
    p_high = np.where(high_vol, l, h)
    p_low = np.where(high_vol, h, l)

    sw_leg, in_leg = _Leg(swing), _Leg(internal)
    sw_hi, sw_lo, in_hi, in_lo = _Pivot(), _Pivot(), _Pivot(), _Pivot()
    sw_trend, in_trend = [0], [0]
    crosses = {k: _Cross(k[1] == "up") for k in
               (("in", "up"), ("in", "dn"), ("sw", "up"), ("sw", "dn"))}
    obs = []   # internal order blocks: [atas, bawah, bias], terbaru di depan

    def hit(name, side, j, level):
        arr = getattr(r, f"{name.lower()}_{'buy' if side == 'BUY' else 'sell'}")
        arr[j] = True
        key = (name, side, j)
        r.levels[key] = level if key not in r.levels else "swing+internal"

    def display(j, is_internal):
        hi, lo = (in_hi, in_lo) if is_internal else (sw_hi, sw_lo)
        trend = in_trend if is_internal else sw_trend
        tag_level = "internal" if is_internal else "swing"
        pre = "in" if is_internal else "sw"
        # bullish
        extra = (not np.isnan(sw_hi.current) and in_hi.current != sw_hi.current) if is_internal else True
        if crosses[(pre, "up")](c[j], hi.current) and not hi.crossed and extra:
            hit("CHOCH" if trend[0] == BEARISH else "BOS", "BUY", j, tag_level)
            hi.crossed = True
            trend[0] = BULLISH
            if is_internal:
                seg = p_low[hi.index:j]
                k = hi.index + int(np.argmin(seg))
                obs.insert(0, [p_high[k], p_low[k], BULLISH])
                del obs[MAX_STORED_OB:]
        # bearish
        extra = (not np.isnan(sw_lo.current) and in_lo.current != sw_lo.current) if is_internal else True
        if crosses[(pre, "dn")](c[j], lo.current) and not lo.crossed and extra:
            hit("CHOCH" if trend[0] == BULLISH else "BOS", "SELL", j, tag_level)
            lo.crossed = True
            trend[0] = BEARISH
            if is_internal:
                seg = p_high[lo.index:j]
                k = lo.index + int(np.argmax(seg))
                obs.insert(0, [p_high[k], p_low[k], BEARISH])
                del obs[MAX_STORED_OB:]

    for j in range(n):
        _structure(sw_leg, sw_hi, sw_lo, h, l, j)
        _structure(in_leg, in_hi, in_lo, h, l, j)
        display(j, True)
        display(j, False)
        # deleteOrderBlocks(true): mitigasi High/Low = "OB breakout"
        idx = 0
        while idx < len(obs):   # meniru for..in Pine yang menghapus elemen saat iterasi
            top, bot, bias = obs[idx]
            if bias == BEARISH and h[j] > top:
                r.ob_buy[j] = True
                r.zones[("OB", "BUY", j)] = (bot, top)
                obs.pop(idx)
            elif bias == BULLISH and l[j] < bot:
                r.ob_sell[j] = True
                r.zones[("OB", "SELL", j)] = (bot, top)
                obs.pop(idx)
            idx += 1

    # drawFairValueGaps() dengan Auto Threshold, timeframe chart
    delta = np.full(n, np.nan)
    delta[1:] = (c[:-1] - o[:-1]) / (o[:-1] * 100)            # barDeltaPercent (candle sebelumnya)
    cum = np.nancumsum(np.abs(np.nan_to_num(delta)))
    with np.errstate(divide="ignore", invalid="ignore"):
        thr = cum / np.arange(n) * 2                            # ta.cum(...)/bar_index*2
    for j in range(2, n):
        if l[j] > h[j - 2] and c[j - 1] > h[j - 2] and delta[j] > thr[j]:
            r.fvg_buy[j] = True
            r.zones[("FVG", "BUY", j)] = (h[j - 2], l[j])
        if h[j] < l[j - 2] and c[j - 1] < l[j - 2] and -delta[j] > thr[j]:
            r.fvg_sell[j] = True
            r.zones[("FVG", "SELL", j)] = (h[j], l[j - 2])
    return r
