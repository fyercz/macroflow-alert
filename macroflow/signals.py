"""Logika sinyal MacroFlow V65 (tanpa scanner/tabel/fibo)."""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ta

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


@dataclass
class Params:
    ut_key: float = 2.0
    ut_period: int = 10
    stoch_k: int = 14
    stoch_d: int = 3
    stoch_s: int = 3
    meter_ema: int = 50
    meter_rsi: int = 14
    meter_adx: int = 14
    meter_thres: int = 20
    meter_ma: str = "EMA"      # MA untuk Meter STRONG: "EMA" (saham) atau "SMA" (alert indeks)
    heikin_ashi: bool = True   # indikator dihitung dari candle Heikin Ashi (seperti chart TradingView HA)
    atr_mult_sl: float = 2.0


# ----------------------------------------------------------------------
# Pembentukan bar timeframe tinggi (H4 / D / W) "as of" suatu waktu
# ----------------------------------------------------------------------
def _agg(df, key):
    g = df.groupby(key, sort=True)
    out = pd.DataFrame({
        "Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
        "Close": g["Close"].last(), "Volume": g["Volume"].sum(),
    })
    return out


def h4_from_hourly(hourly):
    """TradingView IDX H4: 09:00-13:00 (sesi 1) dan 13:00-17:00 (sesi 2)."""
    if hourly is None or hourly.empty:
        return hourly
    idx = hourly.index
    key = idx.normalize() + pd.to_timedelta(np.where(idx.hour < 13, 9, 13), unit="h")
    return _agg(hourly, key)


def weekly_from_daily(daily):
    key = daily.index.normalize() - pd.to_timedelta(daily.index.weekday, unit="D")
    return _agg(daily, key)


def daily_upto(daily, hourly, cutoff):
    """Bar harian yang 'terlihat' pada waktu cutoff (bar hari berjalan bisa parsial)."""
    day = cutoff.normalize()
    if cutoff == day:  # cutoff tepat tengah malam -> hari sebelumnya sudah lengkap
        return daily[daily.index < day]
    past = daily[daily.index < day]
    if hourly is not None and not hourly.empty:
        today = hourly[(hourly.index >= day) & (hourly.index < cutoff)]
        if not today.empty:
            bar = _agg(today, pd.Index([day] * len(today)))
            return pd.concat([past, bar])
    return past


# ----------------------------------------------------------------------
# Meter (f_get_meter_data)
# ----------------------------------------------------------------------
def candles(df, p: Params):
    """OHLC yang dipakai indikator: Heikin Ashi (default) atau candle biasa.

    Input SELALU candle biasa (harga asli); konversi HA dilakukan di sini, termasuk untuk
    timeframe tinggi (H4/D/W disusun dari candle biasa, lalu dijadikan HA — sama dengan
    request.security di chart Heikin Ashi TradingView).
    """
    return ta.heikin_ashi(df) if p.heikin_ashi else df


def meter(df, p: Params):
    """df = candle biasa (dikonversi ke HA sesuai setelan)."""
    df = candles(df, p)
    c, h, l = (df[k].to_numpy(float) for k in ("Close", "High", "Low"))
    ma = ta.sma if str(p.meter_ma).upper() == "SMA" else ta.ema
    e = ma(c, p.meter_ema)
    r = ta.rsi(c, p.meter_rsi)
    _, _, adx = ta.dmi(h, l, c, p.meter_adx, p.meter_adx)
    bull = (c > e) & (r > 50)
    bear = (c < e) & (r < 50)
    strong = adx > p.meter_thres
    score = np.where(bull, 1, np.where(bear, -1, 0))
    txt = np.where(bull, np.where(strong, "STRONG BUY", "BUY"),
                   np.where(bear, np.where(strong, "STRONG SELL", "SELL"), "NEUTRAL"))
    return score, txt


def _last_score(df, p):
    if df is None or len(df) < 2:
        return 0
    return int(meter(df, p)[0][-1])


def htf_scores(daily, hourly, cutoff, p: Params):
    """Skor meter H4, D, W (s3, s4, s5) seperti request.security saat realtime."""
    # histori dipotong secukupnya (EMA50/RSI/ADX sudah konvergen) supaya cepat
    if hourly is not None:
        hh = hourly.iloc[:hourly.index.searchsorted(cutoff)]
        h4 = h4_from_hourly(hh.iloc[-900:])
    else:
        hh, h4 = None, None
    d_full = daily_upto(daily.iloc[:daily.index.searchsorted(cutoff)], hh, cutoff)
    d = d_full.iloc[-400:]
    w = weekly_from_daily(d_full) if len(d_full) else None
    s3 = _last_score(h4, p) if h4 is not None and len(h4) else 0
    return s3, _last_score(d, p), _last_score(w, p)


# ----------------------------------------------------------------------
# Sinyal chart utama
# ----------------------------------------------------------------------
@dataclass
class Signal:
    name: str          # ROYAL / COMBO / GOLDEN / STANDAR / METER
    side: str          # BUY / SELL
    bar_time: pd.Timestamp
    close: float
    info: dict = field(default_factory=dict)


def evaluate(chart, daily, hourly, tf, p: Params, last_n=1):
    """Hitung sinyal pada `last_n` bar terakhir chart.

    chart  : OHLCV timeframe utama (D atau 1H)
    daily  : OHLCV harian (untuk D/W)
    hourly : OHLCV 1 jam (untuk H4)
    tf     : "D" atau "1H"
    """
    n = len(chart)
    if n < 30:
        return []
    # indikator dari candle Heikin Ashi; harga yang ditampilkan & SL/TP dari harga asli
    src = candles(chart, p)
    o, h, l, c, v = (src[k].to_numpy(float) for k in OHLCV)
    rc, rh, rl = (chart[k].to_numpy(float) for k in ("Close", "High", "Low"))

    ema50 = ta.ema(c, 50)
    ema200 = ta.ema(c, 200)
    rsi14 = ta.rsi(c, 14)
    m_line, m_sig, _ = ta.macd(c, 12, 26, 9)
    k = ta.sma(ta.stoch(c, h, l, p.stoch_k), p.stoch_s)
    d = ta.sma(k, p.stoch_d)
    stop, pos = ta.illu_trx(c, h, l, p.ut_key, p.ut_period)
    vol_sma = ta.sma(v, 20)
    atr14 = ta.atr(rh, rl, rc, 14)  # untuk SL: ATR dari harga asli

    pos_prev = np.concatenate([[0], pos[:-1]])
    std_buy = (pos == 1) & (pos_prev == -1)
    std_sell = (pos == -1) & (pos_prev == 1)

    bull_c = (pos == 1) & (c > ema50) & (m_line > m_sig) & (rsi14 > 50)
    bear_c = (pos == -1) & (c < ema50) & (m_line < m_sig) & (rsi14 < 50)
    prev = lambda x: np.concatenate([[False], x[:-1]])
    combo_buy = bull_c & ~prev(bull_c)
    combo_sell = bear_c & ~prev(bear_c)

    golden = (c > ema200) & ta.crossover(k, d) & (k < 30)

    _, mtxt = meter(chart, p)
    mtxt_prev = np.concatenate([[""], mtxt[:-1]])
    meter_buy = (mtxt == "STRONG BUY") & (mtxt_prev != "STRONG BUY")
    meter_sell = (mtxt == "STRONG SELL") & (mtxt_prev != "STRONG SELL")

    vol_ok_buy = (v > vol_sma) & (c > o)
    vol_ok_sell = (v > vol_sma) & (c < o)

    step = pd.Timedelta(days=1) if tf == "D" else pd.Timedelta(hours=1)
    # ROYAL butuh skor H4/D/W per bar -> hitung hanya untuk bar yang dibutuhkan
    royal_buy = np.zeros(n, bool)
    royal_sell = np.zeros(n, bool)
    first = max(1, n - last_n)
    scores = {}
    for i in range(first - 1, n):
        if vol_ok_buy[i] or vol_ok_sell[i]:  # ROYAL mustahil tanpa syarat volume
            scores[i] = htf_scores(daily, hourly, chart.index[i] + step, p)
            s3, s4, s5 = scores[i]
            royal_buy[i] = s3 == 1 and s4 == 1 and s5 == 1 and vol_ok_buy[i]
            royal_sell[i] = s3 == -1 and s4 == -1 and s5 == -1 and vol_ok_sell[i]

    out = []
    for i in range(first, n):
        info = {
            "stop": stop[i], "rsi": rsi14[i],
            "vol_ratio": v[i] / vol_sma[i] if vol_sma[i] else np.nan,
            "chg": (rc[i] / rc[i - 1] - 1) * 100 if rc[i - 1] else np.nan,
            "htf": scores.get(i),
        }
        t = chart.index[i]
        checks = [
            ("ROYAL", "BUY", royal_buy[i] and not royal_buy[i - 1]),
            ("ROYAL", "SELL", royal_sell[i] and not royal_sell[i - 1]),
            ("COMBO", "BUY", combo_buy[i]),
            ("COMBO", "SELL", combo_sell[i]),
            ("GOLDEN", "BUY", golden[i]),
            ("STANDAR", "BUY", std_buy[i]),
            ("STANDAR", "SELL", std_sell[i]),
            ("METER", "BUY", meter_buy[i]),
            ("METER", "SELL", meter_sell[i]),
        ]
        for name, side, hit in checks:
            if hit:
                sig = Signal(name, side, t, rc[i], dict(info))
                if side == "BUY":
                    sig.info.update(sl_tp(rc[i], atr14[i], p))
                out.append(sig)
    return out


# ----------------------------------------------------------------------
# SL / TP (dari bagian Money Management Pine, tanpa hitungan lot)
# ----------------------------------------------------------------------
def tick_size(price):
    """Fraksi harga BEI."""
    if price < 200:
        return 1
    if price < 500:
        return 2
    if price < 2000:
        return 5
    if price < 5000:
        return 10
    return 25


def _round_down(x):
    t = tick_size(x)
    return float(np.floor(x / t) * t)


def sl_tp(entry, atr_val, p: Params):
    """SL = entry - ATR14 x multiplier, TP1 = 2R, TP2 = 3R."""
    if np.isnan(atr_val):
        return {}
    sl = max(entry - atr_val * p.atr_mult_sl, 0)
    risk = entry - sl
    if risk <= 0:
        return {}
    return {"sl": _round_down(sl), "tp1": _round_down(entry + risk * 2),
            "tp2": _round_down(entry + risk * 3)}
