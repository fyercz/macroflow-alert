"""Jalankan CLI dengan yfinance palsu berbentuk data Yahoo asli (index "Date"/"Datetime", tz, MultiIndex).

Contoh:  python tests/yahoo_sim.py ihsg-study
         python tests/yahoo_sim.py run hourly daily-final --dry-run
Hapus state.json / *.xlsx yang terbentuk setelahnya.
"""
import sys, numpy as np, pandas as pd
sys.path[:0] = ['tests', '.']
import yfinance as yf
import test_smoke as t

def fake(syms, period, interval, group_by="ticker", **kw):
    syms = [syms] if isinstance(syms, str) else syms
    frames = {}
    for i, s in enumerate(syms):
        days = 2500 if interval == "1d" else 480
        h = t.make_hourly(i + 3, 6000 if s == "^JKSE" else 1000 + 50 * i, days=days, end=pd.Timestamp("2026-10-19"))
        h.loc[h.index[-1], "Close"] = np.nan  # baris kosong seperti Yahoo
        df = t.daily_from_hourly(h) if interval == "1d" else h
        idx = df.index.tz_localize("Asia/Jakarta")
        idx.name = "Date" if interval == "1d" else "Datetime"
        df = df.copy(); df.index = idx
        if s == "^JKSE":
            df["Volume"] = 0
        df["Adj Close"] = df["Close"]
        frames[s] = df
    out = pd.concat(frames, axis=1, names=["Ticker", "Price"])
    return out

yf.download = fake
from macroflow import main, data
data.load_universe = lambda cfg, f, max_age_days=7: ["AAAA", "BBBB", "CCCC", "BBCA"]
sys.argv = ["x"] + sys.argv[1:]
main.main()
