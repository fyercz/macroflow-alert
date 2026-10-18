"""Pengambilan data: daftar saham IDX + harga dari Yahoo Finance."""
import logging
import time
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)
TZ = "Asia/Jakarta"
# kode khusus (mis. indeks) -> simbol Yahoo; diisi dari config
YAHOO_SYMBOLS = {}
TV_SCAN_URL = "https://scanner.tradingview.com/indonesia/scan"


def fetch_idx_tickers():
    """Ambil semua saham biasa IDX dari screener publik TradingView."""
    body = {
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "subtype", "operation": "in_range", "right": ["common", "foreign-issuer"]},
            {"left": "exchange", "operation": "equal", "right": "IDX"},
        ],
        "columns": ["name"],
        "range": [0, 3000],
        "markets": ["indonesia"],
        "options": {"lang": "en"},
    }
    r = requests.post(TV_SCAN_URL, json=body, timeout=30,
                      headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    names = sorted({row["s"].split(":")[1] for row in r.json()["data"]})
    # buang waran/right/ETF yang kadang ikut (kode saham IDX = 4 huruf)
    return [t for t in names if len(t) == 4 and t.isalpha()]


def load_universe(cfg, cache_file: Path, max_age_days=7):
    manual = [t.strip().upper() for t in cfg.get("tickers") or [] if t.strip()]
    if manual:
        return manual
    cached = cache_file.read_text().split() if cache_file.exists() else []
    fresh = len(cached) > 300 and (time.time() - cache_file.stat().st_mtime) < max_age_days * 86400
    if not fresh:
        try:
            tickers = fetch_idx_tickers()
            if len(tickers) > 300:
                cache_file.write_text("\n".join(tickers) + "\n")
                log.info("Daftar saham diperbarui: %d saham", len(tickers))
        except Exception as e:  # pakai cache lama
            log.warning("Gagal update daftar saham (%s), pakai cache", e)
    tickers = [t.strip() for t in cache_file.read_text().split() if t.strip()] if cache_file.exists() else []
    if not tickers:
        raise RuntimeError("Daftar saham tidak tersedia (tickers.txt kosong & TradingView gagal)")
    return tickers


def _normalize(df, intraday):
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"]).copy()
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert(TZ).tz_localize(None)
    if not intraday:
        idx = idx.normalize()
    df.index = idx
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df["Volume"] = df["Volume"].fillna(0)
    return df


def download(tickers, period, interval, chunk=80, retries=2):
    """Download banyak saham sekaligus. Return dict {ticker: DataFrame}."""
    import yfinance as yf

    out = {}
    intraday = interval not in ("1d", "1wk")
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        syms = [YAHOO_SYMBOLS.get(t, f"{t}.JK") for t in part]
        for attempt in range(retries + 1):
            try:
                raw = yf.download(syms, period=period, interval=interval, group_by="ticker",
                                  auto_adjust=False, threads=True, progress=False)
                break
            except Exception as e:
                log.warning("download gagal (%s), coba lagi", e)
                time.sleep(5 * (attempt + 1))
        else:
            continue
        if raw is None or raw.empty:
            continue
        for t, s in zip(part, syms):
            try:
                df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = _normalize(df, intraday)
                if len(df):
                    out[t] = df
            except KeyError:
                pass
    log.info("Data %s: %d/%d saham", interval, len(out), len(tickers))
    return out


def merge_today(daily, hourly, today):
    """Kalau bar harian hari ini belum muncul di Yahoo, bentuk dari data 1 jam."""
    if hourly is None or hourly.empty or (len(daily) and daily.index[-1] >= today):
        return daily
    h = hourly[hourly.index >= today]
    if h.empty:
        return daily
    bar = pd.DataFrame({
        "Open": [h["Open"].iloc[0]], "High": [h["High"].max()], "Low": [h["Low"].min()],
        "Close": [h["Close"].iloc[-1]], "Volume": [h["Volume"].sum()],
    }, index=[today])
    return pd.concat([daily, bar])
