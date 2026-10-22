"""Smart Money Concepts (SMC): BOS, CHoCH, Order Block, Fair Value Gap.

Semua tanpa repaint — setiap kejadian hanya memakai data sampai candle itu:
- Swing high/low = pivot dengan `swing` candle di kiri & kanan, baru "diketahui" `swing` candle
  sesudahnya (seperti ta.pivothigh di Pine).
- BOS / CHoCH: close menembus swing high (bullish) / swing low (bearish) terakhir yang belum ditembus.
  CHoCH bila arah struktur sebelumnya berlawanan (pembalikan), BOS bila searah (lanjutan).
- Order Block: saat struktur ditembus, candle dengan low terendah (bullish OB) / high tertinggi
  (bearish OB) antara swing yang ditembus dan candle penembus menjadi zona demand / supply.
  Sinyal OB = retest pertama zona itu; zona batal bila close menembus sisi seberangnya.
- Fair Value Gap: celah 3 candle (low[i] > high[i-2] bullish, high[i] < low[i-2] bearish) yang
  candle tengahnya cukup besar (> 2× rata-rata |body %| sejauh ini, seperti "auto threshold" LuxAlgo).
Input: OHLC candle yang dipakai indikator (Heikin Ashi bila aktif).
"""
from dataclasses import dataclass, field

import numpy as np

MAX_OB = 5  # zona aktif maksimum per sisi


@dataclass
class SMC:
    choch_buy: np.ndarray
    choch_sell: np.ndarray
    bos_buy: np.ndarray
    bos_sell: np.ndarray
    ob_buy: np.ndarray
    ob_sell: np.ndarray
    fvg_buy: np.ndarray
    fvg_sell: np.ndarray
    zones: dict = field(default_factory=dict)   # (nama, arah, i) -> (bawah, atas)


def _pivots(h, l, swing):
    """Indeks pivot high/low (dikonfirmasi `swing` bar setelahnya)."""
    n = len(h)
    ph, pl = np.zeros(n, bool), np.zeros(n, bool)
    for i in range(swing, n - swing):
        wh = h[i - swing:i + swing + 1]
        wl = l[i - swing:i + swing + 1]
        ph[i] = h[i] == wh.max() and (wh[:swing] < h[i]).all()
        pl[i] = l[i] == wl.min() and (wl[:swing] > l[i]).all()
    return ph, pl


def detect(o, h, l, c, swing=5):
    n = len(c)
    z = lambda: np.zeros(n, bool)
    r = SMC(z(), z(), z(), z(), z(), z(), z(), z())
    if n < 2 * swing + 3:
        return r
    ph, pl = _pivots(h, l, swing)

    top = bot = None          # (level, index pivot, sudah ditembus?)
    trend = 0                 # 1 bullish, -1 bearish
    bull_obs, bear_obs = [], []   # [bawah, atas, i_terbentuk, sudah_retest]
    for j in range(n):
        # pivot yang terkonfirmasi di bar j (pivot di j-swing)
        i = j - swing
        if i >= swing:
            if ph[i]:
                top = [h[i], i, False]
            if pl[i]:
                bot = [l[i], i, False]

        # mitigasi & retest order block (memakai zona yang terbentuk sebelum bar j)
        for ob in list(bull_obs):
            if c[j] < ob[0]:
                bull_obs.remove(ob)
            elif not ob[3] and j > ob[2] and l[j] <= ob[1] and c[j] >= ob[0]:
                ob[3] = True
                r.ob_buy[j] = True
                r.zones[("OB", "BUY", j)] = (ob[0], ob[1])
        for ob in list(bear_obs):
            if c[j] > ob[1]:
                bear_obs.remove(ob)
            elif not ob[3] and j > ob[2] and h[j] >= ob[0] and c[j] <= ob[1]:
                ob[3] = True
                r.ob_sell[j] = True
                r.zones[("OB", "SELL", j)] = (ob[0], ob[1])

        # break of structure / change of character
        if j > 0 and top is not None and not top[2] and c[j] > top[0] and c[j - 1] <= top[0]:
            top[2] = True
            (r.choch_buy if trend == -1 else r.bos_buy)[j] = True
            trend = 1
            k = top[1] + int(np.argmin(l[top[1]:j]))  # candle low terendah sebelum penembusan
            bull_obs.append([l[k], h[k], j, False])
            del bull_obs[:-MAX_OB]
        if j > 0 and bot is not None and not bot[2] and c[j] < bot[0] and c[j - 1] >= bot[0]:
            bot[2] = True
            (r.choch_sell if trend == 1 else r.bos_sell)[j] = True
            trend = -1
            k = bot[1] + int(np.argmax(h[bot[1]:j]))
            bear_obs.append([l[k], h[k], j, False])
            del bear_obs[:-MAX_OB]

    # fair value gap
    with np.errstate(divide="ignore", invalid="ignore"):
        body = np.abs(c - o) / o
    thr = 2 * np.nancumsum(body) / np.arange(1, n + 1)
    for j in range(2, n):
        mid = (c[j - 1] - o[j - 1]) / o[j - 1] if o[j - 1] else 0
        if l[j] > h[j - 2] and c[j - 1] > h[j - 2] and mid > thr[j - 1]:
            r.fvg_buy[j] = True
            r.zones[("FVG", "BUY", j)] = (h[j - 2], l[j])
        if h[j] < l[j - 2] and c[j - 1] < l[j - 2] and -mid > thr[j - 1]:
            r.fvg_sell[j] = True
            r.zones[("FVG", "SELL", j)] = (h[j], l[j - 2])
    return r
