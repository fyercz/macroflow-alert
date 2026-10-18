"""Tes offline MacroFlow (data sintetis, tanpa internet).

Jalankan:  python tests/test_smoke.py      (atau: python -m pytest tests)
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macroflow import backtest, data, indicators as ta, main, market, notify  # noqa: E402
from macroflow.signals import Params, evaluate, h4_from_hourly, meter, sl_tp, tick_size  # noqa: E402

WIB = ZoneInfo("Asia/Jakarta")
TODAY = pd.Timestamp("2026-10-16")  # Jumat
NOW = datetime(2026, 10, 16, 16, 20, tzinfo=WIB)


# ----------------------------------------------------------------------
# Data sintetis
# ----------------------------------------------------------------------
def make_hourly(seed, start_price=1000.0, days=260, end=TODAY):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=end, periods=days)
    hours = [9, 10, 11, 13, 14, 15]
    idx = pd.DatetimeIndex([d + pd.Timedelta(hours=h) for d in dates for h in hours])
    t = np.arange(len(idx))
    drift = np.sin(t / 90.0) * 0.004 + np.sin(t / 23.0) * 0.002
    c = start_price * np.exp(np.cumsum(drift + rng.normal(0, 0.008, len(idx))))
    o = np.concatenate([[c[0]], c[:-1]]) * (1 + rng.normal(0, 0.002, len(idx)))
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, len(idx))))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, len(idx))))
    v = rng.integers(200_000, 3_000_000, len(idx)).astype(float)
    return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c, "Volume": v}, index=idx)


def daily_from_hourly(h):
    g = h.groupby(h.index.normalize())
    return pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(),
                         "Low": g["Low"].min(), "Close": g["Close"].last(),
                         "Volume": g["Volume"].sum()})


TICKERS = ["AAAA", "BBBB", "CCCC", "BBCA"]
HOURLY = {t: make_hourly(i, 500 + 800 * i) for i, t in enumerate(TICKERS)}
HOURLY["IHSG"] = make_hourly(99, 7000)
DAILY = {t: daily_from_hourly(h) for t, h in HOURLY.items()}


def fake_download(tickers, period, interval, **kw):
    src = DAILY if interval == "1d" else HOURLY
    return {t: src[t].copy() for t in tickers if t in src}


def patch_data():
    data.download = fake_download
    data.load_universe = lambda cfg, cache_file, max_age_days=7: [t for t in TICKERS]


CFG = {
    "tickers": [],
    "indices": {"IHSG": {"yahoo": "^JKSE", "tradingview": "IDX:COMPOSITE",
                         "signals": ["COMBO", "GOLDEN", "STANDAR", "METER"]}},
    "filter": {"min_avg_value": 1e8, "min_price": 50},
    "signals": {"D": ["ROYAL", "COMBO", "GOLDEN", "STANDAR", "METER"],
                "1H": {"BUY": ["COMBO", "METER"], "SELL": ["ROYAL", "COMBO", "STANDAR", "METER"]}},
    "market_warning": {"index": "IHSG", "window_days": 5, "min_events": 2},
}


def make_crash_index(seed=99, amp=0.05, drop=0.20, k=30):
    """Indeks naik stabil lalu crash bergelombang di 5 hari terakhir (STRONG SELL berulang)."""
    h = make_hourly(seed, 7000)
    n = len(h)
    rng = np.random.default_rng(1)
    base = 7000 * np.exp(np.arange(n) * 0.0006 + rng.normal(0, 0.002, n).cumsum() * 0.2)
    f = base / h["Close"].to_numpy()
    seg = np.linspace(0, 1, k)
    f[-k:] *= 1 - drop * seg + amp * np.sin(seg * 4 * np.pi)
    for c in ("Open", "High", "Low", "Close"):
        h[c] = h[c].to_numpy() * f
    return h


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True

    def send_all(self, msgs):
        self.sent.extend(msgs)


# ----------------------------------------------------------------------
# Tes
# ----------------------------------------------------------------------
def test_indicators():
    x = np.arange(1, 31, dtype=float)
    e = ta.ema(x, 10)
    assert np.isnan(e[8]) and e[9] == x[:10].mean()  # seed SMA seperti Pine
    assert np.allclose(ta.sma(x, 5)[4], 3.0)
    r = ta.rsi(x, 14)
    assert r[-1] == 100.0  # naik terus -> RSI 100
    h = HOURLY["AAAA"]
    hi, lo, c = (h[k].to_numpy(float) for k in ("High", "Low", "Close"))
    atr = ta.atr(hi, lo, c, 14)
    assert np.all(atr[20:] > 0)
    stop, pos = ta.illu_trx(c, hi, lo, 2.0, 10)
    assert set(np.unique(pos)) <= {-1, 0, 1} and np.isfinite(stop[-1])
    assert list(ta.crossover(np.array([1., 2., 3.]), np.array([2., 2., 2.]))) == [False, False, True]


def test_tick_and_sltp():
    assert [tick_size(p) for p in (100, 300, 1000, 3000, 9000)] == [1, 2, 5, 10, 25]
    r = sl_tp(1000.0, 20.0, Params())
    assert r == {"sl": 960.0, "tp1": 1080.0, "tp2": 1120.0}
    r = sl_tp(5100.0, 37.0, Params())
    assert all(v % 25 == 0 for v in r.values())
    assert sl_tp(1000.0, np.nan, Params()) == {}


def test_h4_and_meter():
    h4 = h4_from_hourly(HOURLY["AAAA"])
    assert set(h4.index.hour) == {9, 13}
    score, txt = meter(DAILY["AAAA"], Params())
    assert set(np.unique(score)) <= {-1, 0, 1} and len(txt) == len(DAILY["AAAA"])


def test_evaluate_runs():
    p = Params()
    total = 0
    for t in TICKERS:
        d, h = DAILY[t], HOURLY[t]
        for tf, chart in (("D", d), ("1H", h)):
            sigs = evaluate(chart, d, h, tf, p, last_n=120)
            total += len(sigs)
            for s in sigs:
                assert s.name in notify.ORDER and s.side in ("BUY", "SELL")
                if s.side == "BUY" and "sl" in s.info:
                    assert s.info["sl"] < s.close < s.info["tp1"] < s.info["tp2"]
    assert total > 0, "data sintetis seharusnya menghasilkan sinyal"


def test_auto_modes():
    def at(h, m, day=19):
        return datetime(2026, 10, day, h, m, tzinfo=WIB)
    assert main.auto_modes(at(10, 15)) == ["hourly"]
    assert main.auto_modes(at(14, 45)) == ["hourly", "daily-preclose"]
    assert main.auto_modes(at(16, 15)) == ["hourly", "daily-final"]
    assert main.auto_modes(at(18, 0)) == ["daily-final"]
    assert main.auto_modes(at(10, 15, day=18)) == []  # Minggu


def test_notify_split():
    from macroflow.signals import Signal
    notify.TV_SYMBOLS.clear()
    items = [(f"T{i:03d}", Signal("STANDAR", "BUY", TODAY, 1000.0,
                                   {"chg": 1.0, "rsi": 55, "vol_ratio": 1.2,
                                    "sl": 950, "tp1": 1100, "tp2": 1150}))
             for i in range(150)]
    msgs = notify.build_messages("HDR", items)
    assert len(msgs) > 1 and all(len(m) <= 3900 for m in msgs)
    assert sum(m.count("<a href=") for m in msgs) == 150
    assert notify.build_messages("HDR", []) == []


def test_run_modes_and_dedup():
    patch_data()
    notify.TV_SYMBOLS.clear()
    data.YAHOO_SYMBOLS.clear()
    with tempfile.TemporaryDirectory() as tmp:
        state = main.State(Path(tmp) / "state.json")
        tg = FakeTelegram()
        main.run_modes(["hourly", "daily-final"], CFG, NOW, tg, state)
        assert tg.sent, "run pertama seharusnya mengirim pesan"
        assert (Path(tmp) / "state.json").exists()
        assert state.d["done"].get("daily-final|2026-10-16") is True
        n_sent = len(state.d["sent"])
        # run kedua: final tidak dikirim ulang, sinyal 1H tidak dobel
        tg2 = FakeTelegram()
        main.run_modes(["hourly", "daily-final"], CFG, NOW, tg2, state)
        assert len(state.d["sent"]) == n_sent
        assert not any("SINYAL FINAL" in m for m in tg2.sent)
        assert not any("SINYAL 1H" in m for m in tg2.sent)


def test_backtest_excel():
    patch_data()
    data.YAHOO_SYMBOLS.clear()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "bt.xlsx"
        df, summary = backtest.run(
            CFG, "2026-08-01", None, ("D", "1H"), out,
            index_sigs=main.setup_indices(CFG), enabled=main.enabled, allowed=main.allowed,
            params=Params(),
            liquid=main.liquid, root=Path(tmp), now=pd.Timestamp(NOW.replace(tzinfo=None)))
        assert not df.empty and not summary.empty
        assert out.exists()
        sheets = pd.ExcelFile(out).sheet_names
        assert "Ringkasan" in sheets and "Catatan" in sheets
        txt = backtest.telegram_summary(summary, pd.Timestamp("2026-08-01"), None)
        assert "BACKTEST" in txt


def test_cmd_backtest_end_tf():
    import argparse
    patch_data()
    data.YAHOO_SYMBOLS.clear()
    old_root = main.ROOT
    with tempfile.TemporaryDirectory() as tmp:
        main.ROOT = Path(tmp)
        try:
            args = argparse.Namespace(start=" 2026-08-01", end="2026-09-15", tf=["D"],
                                      out="bt.xlsx", telegram=False)
            main.cmd_backtest(CFG, args, NOW)
            d = pd.read_excel(Path(tmp) / "bt.xlsx", sheet_name=None)
            assert "Sinyal 1H" not in d and "Sinyal Daily" in d
            w = pd.to_datetime(d["Sinyal Daily"]["Waktu"])
            assert w.min() >= pd.Timestamp("2026-08-01") and w.max() <= pd.Timestamp("2026-09-15")
            for bad in (("2026-13-01", None), ("2026-09-01", "2026-08-01")):
                args = argparse.Namespace(start=bad[0], end=bad[1], tf=["D"], out="x.xlsx",
                                          telegram=False)
                try:
                    main.cmd_backtest(CFG, args, NOW)
                    raise AssertionError("tanggal salah seharusnya ditolak")
                except SystemExit as e:
                    assert e.code == 1
        finally:
            main.ROOT = old_root


def test_enabled_config():
    import yaml
    cfg = yaml.safe_load((Path(main.ROOT) / "config.yaml").read_text())
    d, h = main.enabled(cfg, "D"), main.enabled(cfg, "1H")
    assert not any(n == "GOLDEN" for n, _ in d | h), "GOLDEN harus mati"
    assert {n for n, side in h if side == "BUY"} == {"COMBO", "METER"}
    assert ("ROYAL", "SELL") in h and ("ROYAL", "BUY") in d
    assert main.enabled({"signals": {"D": ["COMBO"]}}, "D") == {("COMBO", "BUY"), ("COMBO", "SELL")}
    from macroflow.signals import Signal
    sig = Signal("STANDAR", "BUY", TODAY, 1.0)
    assert not main.allowed(sig, "AAAA", h, {})
    assert main.allowed(Signal("COMBO", "BUY", TODAY, 1.0), "AAAA", h, {})
    assert not main.allowed(Signal("COMBO", "BUY", TODAY, 1.0), "IHSG", h, {"IHSG": {"METER"}})


def test_merge_messages():
    from macroflow.signals import Signal
    notify.TV_SYMBOLS.clear()
    info = {"chg": 1.0, "rsi": 60, "vol_ratio": 2.0, "sl": 950, "tp1": 1100, "tp2": 1150}
    items = [("AAAA", Signal("METER", "BUY", TODAY, 1000.0, dict(info))),
             ("AAAA", Signal("COMBO", "BUY", TODAY, 1000.0, dict(info))),
             ("BBBB", Signal("STANDAR", "SELL", TODAY, 500.0, {"stop": 520})),
             ("CCCC", Signal("COMBO", "BUY", TODAY, 800.0, dict(info)))]
    msg = "\n".join(notify.build_messages("HDR", items))
    assert msg.count(">AAAA<") == 1 and "🔷COMBO + 📶STRONG" in msg
    assert "🟢 <b>BUY</b> — 2 saham" in msg and "🔴 <b>SELL</b> — 1 saham" in msg
    assert msg.index(">AAAA<") < msg.index(">CCCC<")  # sinyal terbanyak di atas
    long = [(f"T{i:03d}", Signal("COMBO", "BUY", TODAY, 1000.0, dict(info))) for i in range(150)]
    msgs = notify.build_messages("JUDUL\nSTATUS PANJANG", long)
    assert len(msgs) > 1 and msgs[1].startswith("JUDUL (lanjutan)") and "STATUS" not in msgs[1]


def test_market_status_warning():
    now = pd.Timestamp("2026-10-16 16:20")
    h = make_crash_index()
    s = market.ihsg_status(daily_from_hourly(h), h, now, Params())
    assert s.trend == "TURUN" and s.warning and len(s.sells) >= 2
    assert all(e.time >= pd.Timestamp("2026-10-12") and e.side == "SELL" for e in s.sells)
    assert len({(e.time, e.tf) for e in s.sells}) == len(s.sells)  # 1 kejadian per candle
    assert s.regime in ("UPTREND", "DOWNTREND", "TRANSISI") and "Rezim:" in s.text()
    only_meter = market.ihsg_status(daily_from_hourly(h), h, now, Params(), signals=("METER",))
    assert all(e.names == ["METER"] for e in only_meter.sells)
    assert len(only_meter.sells) <= len(s.sells)
    d_only = market.ihsg_status(daily_from_hourly(h), h, now, Params(), timeframes=("D",))
    assert all(e.tf == "D" for e in d_only.sells + d_only.buys)
    txt = s.text()
    assert "PERINGATAN KERAS" in txt and "STATUS IHSG" in txt
    calm = market.ihsg_status(DAILY["IHSG"], HOURLY["IHSG"], now, Params())
    assert calm is not None and "STATUS IHSG" in calm.text()
    strict = market.ihsg_status(daily_from_hourly(h), h, now, Params(), min_events=99)
    assert not strict.warning and "PERINGATAN KERAS" not in strict.text()


def test_run_modes_ihsg_warning():
    patch_data()
    notify.TV_SYMBOLS.clear()
    data.YAHOO_SYMBOLS.clear()
    old_h, old_d = HOURLY["IHSG"], DAILY["IHSG"]
    HOURLY["IHSG"] = make_crash_index()
    DAILY["IHSG"] = daily_from_hourly(HOURLY["IHSG"])
    try:
        with tempfile.TemporaryDirectory() as tmp:
            state = main.State(Path(tmp) / "state.json")
            tg = FakeTelegram()
            main.run_modes(["hourly", "daily-final"], CFG, NOW, tg, state)
            assert tg.sent[0].startswith("🚨🚨🚨 <b>PERINGATAN KERAS")
            assert all("STATUS IHSG" in m for m in tg.sent[1:2])
            tg2 = FakeTelegram()
            main.run_modes(["hourly"], CFG, NOW, tg2, state)
            assert not any(m.startswith("🚨") for m in tg2.sent)  # pesan khusus cukup 1x/hari
    finally:
        HOURLY["IHSG"], DAILY["IHSG"] = old_h, old_d


def test_regime():
    assert market.regime(110, 100, 95) == "UPTREND"
    assert market.regime(90, 100, 105) == "DOWNTREND"
    assert market.regime(90, 100, 95) == "TRANSISI"
    assert market.regime(110, 100, 105) == "TRANSISI"


def test_ihsg_study():
    from macroflow import study
    long_h = make_hourly(7, 6000, days=700)
    long_d = daily_from_hourly(long_h)
    old = data.download
    data.download = lambda tickers, period, interval, **kw: (
        {"IHSG": (long_d if interval == "1d" else long_h[long_h.index >= long_h.index[-6 * 250]]).copy()})
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "study.xlsx"
            summary, reg = study.run(CFG, Params(), out, now=pd.Timestamp("2026-10-16 16:20"))
            assert len(summary) >= 10 and summary["Episode"].sum() > 0
            assert set(pd.ExcelFile(out).sheet_names) >= {"Aturan", "Sinyal per rezim", "Episode", "Harian"}
            assert not reg.empty and set(reg["Arah sinyal"]) <= {"BUY", "SELL"}
            harian = pd.read_excel(out, sheet_name="Harian")
            # aturan dengan 1H hanya dinilai di periode yang punya data 1H
            assert harian["Ada 1H"].sum() < len(harian)
            txt = study.telegram_text(summary, reg)
            assert "STUDI POLA IHSG" in txt and "episode" in txt
    finally:
        data.download = old


if __name__ == "__main__":
    tests = [(n, f) for n, f in dict(globals()).items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"OK    {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"GAGAL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} lulus")
    sys.exit(1 if failed else 0)
