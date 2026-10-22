"""Indikator yang meniru perilaku built-in Pine Script (ta.*).

Semua fungsi menerima / mengembalikan numpy array float. NaN dipakai sebagai
pengganti `na` di Pine.
"""
import numpy as np
import pandas as pd


def _first_valid(src):
    idx = np.where(~np.isnan(src))[0]
    return idx[0] if len(idx) else None


def sma(src, length):
    return pd.Series(src).rolling(length, min_periods=length).mean().to_numpy()


def _recursive_ma(src, length, alpha):
    """EMA/RMA ala Pine: diawali SMA `length` bar pertama, lalu rekursif."""
    src = np.asarray(src, dtype=float)
    out = np.full(len(src), np.nan)
    start = _first_valid(src)
    if start is None or len(src) - start < length:
        return out
    seed_end = start + length - 1
    window = src[start:seed_end + 1]
    if np.isnan(window).any():
        # data bolong di awal: cari jendela pertama yang penuh
        s = pd.Series(src).rolling(length, min_periods=length).mean().to_numpy()
        valid = np.where(~np.isnan(s))[0]
        if not len(valid):
            return out
        seed_end = valid[0]
        out[seed_end] = s[seed_end]
    else:
        out[seed_end] = window.mean()
    prev = out[seed_end]
    for i in range(seed_end + 1, len(src)):
        x = src[i]
        if np.isnan(x):
            out[i] = prev  # pertahankan nilai (jarang terjadi)
            continue
        prev = alpha * x + (1 - alpha) * prev
        out[i] = prev
    return out


def ema(src, length):
    return _recursive_ma(src, length, 2.0 / (length + 1))


def rma(src, length):
    return _recursive_ma(src, length, 1.0 / length)


def change(src):
    out = np.full(len(src), np.nan)
    out[1:] = src[1:] - src[:-1]
    return out


def rsi(src, length):
    ch = change(src)
    up = rma(np.where(np.isnan(ch), np.nan, np.maximum(ch, 0)), length)
    dn = rma(np.where(np.isnan(ch), np.nan, np.maximum(-ch, 0)), length)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(dn == 0, 100.0, np.where(up == 0, 0.0, 100 - 100 / (1 + up / dn)))
    out[np.isnan(up) | np.isnan(dn)] = np.nan
    return out


def true_range(high, low, close):
    prev_close = np.concatenate([[np.nan], close[:-1]])
    tr = np.fmax(high - low, np.fmax(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr[0] = high[0] - low[0]
    return tr


def atr(high, low, close, length):
    return rma(true_range(high, low, close), length)


def dmi(high, low, close, di_len, adx_len):
    up = change(high)
    down = -change(low)
    plus_dm = np.where(np.isnan(up), np.nan, np.where((up > down) & (up > 0), up, 0.0))
    minus_dm = np.where(np.isnan(down), np.nan, np.where((down > up) & (down > 0), down, 0.0))
    trur = rma(true_range(high, low, close), di_len)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus = 100 * rma(plus_dm, di_len) / trur
        minus = 100 * rma(minus_dm, di_len) / trur
        s = plus + minus
        dx = np.abs(plus - minus) / np.where(s == 0, 1, s)
    adx = 100 * rma(dx, adx_len)
    return plus, minus, adx


def macd(src, fast=12, slow=26, signal=9):
    line = ema(src, fast) - ema(src, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def stoch(close, high, low, length):
    hh = pd.Series(high).rolling(length, min_periods=length).max().to_numpy()
    ll = pd.Series(low).rolling(length, min_periods=length).min().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 * (close - ll) / (hh - ll)
    out[~np.isfinite(out)] = np.nan
    return out


def crossover(a, b):
    out = np.zeros(len(a), dtype=bool)
    out[1:] = (a[1:] > b[1:]) & (a[:-1] <= b[:-1])
    return out


def illu_trx(close, high, low, key, period):
    """Illu Trx (UT Bot) trailing stop + posisi, sama persis dengan Pine."""
    n = len(close)
    n_loss = key * atr(high, low, close, period)
    stop = np.full(n, np.nan)
    pos = np.zeros(n, dtype=int)
    for i in range(n):
        prev = 0.0 if i == 0 or np.isnan(stop[i - 1]) else stop[i - 1]
        c = close[i]
        c1 = close[i - 1] if i > 0 else np.nan
        nl = n_loss[i]
        if c > prev and c1 > prev:
            stop[i] = np.nan if np.isnan(nl) else max(prev, c - nl)
        elif c < prev and c1 < prev:
            stop[i] = np.nan if np.isnan(nl) else min(prev, c + nl)
        elif c > prev:
            stop[i] = c - nl
        else:
            stop[i] = c + nl
        p_prev = pos[i - 1] if i > 0 else 0
        if c1 < prev and c > prev:
            pos[i] = 1
        elif c1 > prev and c < prev:
            pos[i] = -1
        else:
            pos[i] = p_prev
    return stop, pos


def heikin_ashi(df):
    """Candle Heikin Ashi seperti TradingView (OHLC baru, Volume tetap).

    HA close = (O+H+L+C)/4 · HA open = (HA open + HA close) candle sebelumnya / 2
    (candle pertama: (O+C)/2) · HA high = max(H, HA open, HA close) · HA low = min(L, HA open, HA close)
    """
    if df is None or not len(df):
        return df
    o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    hc = (o + h + l + c) / 4.0
    ho = np.empty(len(c))
    ho[0] = (o[0] + c[0]) / 2.0
    for i in range(1, len(c)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2.0
    out = df.copy()
    out["Open"] = ho
    out["Close"] = hc
    out["High"] = np.maximum(h, np.maximum(ho, hc))
    out["Low"] = np.minimum(l, np.minimum(ho, hc))
    return out
