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
HOURLY["SP500"] = make_hourly(55, 6500)
DAILY = {t: daily_from_hourly(h) for t, h in HOURLY.items()}


def fake_download(tickers, period, interval, **kw):
    src = DAILY if interval == "1d" else HOURLY
    return {t: src[t].copy() for t in tickers if t in src}


def patch_data():
    data.download = fake_download
    data.load_universe = lambda cfg, cache_file, max_age_days=7: [t for t in TICKERS]


CFG = {
    "tickers": [],
    "indices": {},
    "index_alert": {"meter_ma": "SMA", "timeframes": ["D", "W"], "signals": ["COMBO", "STANDAR", "METER"],
                    "indices": {"IHSG": {"yahoo": "^JKSE", "tradingview": "IDX:COMPOSITE",
                                         "name": "IHSG", "market": "IDX"},
                                "SP500": {"yahoo": "^GSPC", "tradingview": "SP:SPX",
                                          "name": "S&P 500", "market": "US"}}},
    "filter": {"min_avg_value": 1e8, "min_price": 50},
    "signals": {"D": ["ROYAL", "COMBO", "GOLDEN", "STANDAR", "METER"],
                "1H": {"BUY": ["COMBO", "METER"], "SELL": ["ROYAL", "COMBO", "STANDAR", "METER"]}},
    "market_warning": {"index": "IHSG", "window_days": 5, "min_events": 2, "timeframes": ["D", "W"]},
}


def make_crash_index(seed=99, amp=0.12, drop=0.20, k=60):
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
    # default: saham hanya Daily
    assert main.auto_modes(at(10, 15)) == []
    assert main.auto_modes(at(14, 45)) == ["daily-preclose"]
    assert main.auto_modes(at(16, 15)) == ["daily-final", "index-idx"]
    assert not main.hourly_enabled({}) and main.hourly_enabled({"hourly_alerts": True})
    # 1H dinyalakan lagi lewat config
    assert main.auto_modes(at(10, 15), hourly=True) == ["hourly"]
    assert main.auto_modes(at(14, 45), hourly=True) == ["hourly", "daily-preclose"]
    assert main.auto_modes(at(16, 15), hourly=True) == ["hourly", "daily-final", "index-idx"]
    assert main.auto_modes(at(18, 0)) == ["daily-final", "index-idx"]
    assert main.auto_modes(at(10, 15, day=18)) == []  # Minggu
    assert main.auto_modes(at(5, 15, day=20)) == ["index-us"]   # Selasa pagi: S&P Senin
    assert main.auto_modes(at(5, 15, day=24)) == ["index-us"]   # Sabtu pagi: S&P Jumat (+Weekly)
    assert main.auto_modes(at(5, 15, day=19)) == []             # Senin pagi: AS libur akhir pekan
    assert main.auto_modes(at(5, 15, day=25)) == []             # Minggu


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
        assert not any("FINAL SIGNALS" in m for m in tg2.sent)
        assert not any("1H SIGNALS" in m for m in tg2.sent)


def test_two_runs_per_day_cover_all_1h_candles():
    """Jadwal 2x sehari (14:45 & 16:15): semua sinyal 1H hari itu terkirim tepat sekali."""
    patch_data()
    notify.TV_SYMBOLS.clear()
    data.YAHOO_SYMBOLS.clear()
    with tempfile.TemporaryDirectory() as tmp:
        state = main.State(Path(tmp) / "state.json")
        for hh, mm in ((14, 45), (16, 15)):
            now = datetime(2026, 10, 16, hh, mm, tzinfo=WIB)
            assert "hourly" in main.auto_modes(now, hourly=True)
            main.run_modes(["hourly"], CFG, now, FakeTelegram(), state)
        sent = {k for k in state.d["sent"] if "|1H|" in k}
    allow = main.enabled(CFG, "1H")
    expected = set()
    for t in TICKERS:
        h = HOURLY[t][HOURLY[t].index + pd.Timedelta(hours=1) <= pd.Timestamp("2026-10-16 16:15")]
        for sg in evaluate(h, DAILY[t], HOURLY[t], "1H", Params(), last_n=7):
            if sg.bar_time >= TODAY and (sg.name, sg.side) in allow:
                expected.add(main.sig_key(t, "1H", sg))
    assert sent == expected and len(expected) > 0


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
    assert cfg["hourly_alerts"] is False  # saham hanya Daily (pengguna 23 Okt 2026)
    d, h = main.enabled(cfg, "D"), main.enabled(cfg, "1H")
    assert not any(n == "GOLDEN" for n, _ in d | h), "GOLDEN harus mati"
    assert {n for n, side in h if side == "BUY"} == {"COMBO", "METER", "CHOCH", "OB"}
    assert {"CHOCH", "BOS", "OB", "FVG"} <= {n for n, _ in d}
    assert ("ROYAL", "SELL") in h and not any(n == "ROYAL" for n, _ in d)
    assert ("OB", "BUY") not in d and ("FVG", "SELL") not in d and ("OB", "SELL") in d
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
    assert "🟢 <b>BUY</b> — 2 stocks" in msg and "🔴 <b>SELL</b> — 1 stocks" in msg
    assert msg.index(">AAAA<") < msg.index(">CCCC<")  # sinyal terbanyak di atas
    long = [(f"T{i:03d}", Signal("COMBO", "BUY", TODAY, 1000.0, dict(info))) for i in range(150)]
    msgs = notify.build_messages("JUDUL\nSTATUS PANJANG", long)
    assert len(msgs) > 1 and msgs[1].startswith("JUDUL (continued)") and "STATUS" not in msgs[1]


def test_market_status_warning():
    now = pd.Timestamp("2026-10-16 16:20")
    h = make_crash_index()
    s = market.ihsg_status(daily_from_hourly(h), now, Params())
    assert s.trend == "TURUN" and s.warning and len(s.sells) >= 2
    assert all(e.time >= pd.Timestamp("2026-10-12") and e.side == "SELL" for e in s.sells)
    assert len({(e.time, e.tf) for e in s.sells}) == len(s.sells)  # 1 kejadian per candle
    assert s.regime in ("UPTREND", "DOWNTREND", "TRANSISI") and "Regime:" in s.text()
    only_meter = market.ihsg_status(daily_from_hourly(h), now, Params(), signals=("METER",))
    assert all(e.names == ["METER"] for e in only_meter.sells)
    assert len(only_meter.sells) <= len(s.sells)
    d_only = market.ihsg_status(daily_from_hourly(h), now, Params(), timeframes=("D",))
    assert all(e.tf == "D" for e in d_only.sells + d_only.buys)
    txt = s.text()
    assert "STRONG WARNING" in txt and "IHSG STATUS" in txt
    calm = market.ihsg_status(DAILY["IHSG"], now, Params())
    assert calm is not None and "IHSG STATUS" in calm.text()
    strict = market.ihsg_status(daily_from_hourly(h), now, Params(), min_events=99)
    assert all(e.tf in ("D", "W") for e in s.sells + s.buys)
    assert "W: <b>" in txt and "1H" not in txt
    assert not strict.warning and "STRONG WARNING" not in strict.text()


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
            assert tg.sent[0].startswith("🚨🚨🚨 <b>STRONG WARNING")
            assert all("IHSG STATUS" in m for m in tg.sent[1:2])
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
    long_d.index.name = "Date"  # seperti data Yahoo asli (bug 19 Okt: KeyError 'Tanggal')
    old = data.download
    data.download = lambda tickers, period, interval, **kw: (
        {"IHSG": (long_d if interval == "1d" else long_h[long_h.index >= long_h.index[-6 * 250]]).copy()})
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "study.xlsx"
            summary, reg = study.run(CFG, Params(), out, now=pd.Timestamp("2026-10-16 16:20"))
            assert len(summary) >= 9 and summary["Episode"].sum() > 0
            assert set(pd.ExcelFile(out).sheet_names) >= {"Aturan", "Sinyal per rezim", "Episode", "Harian"}
            assert not reg.empty and set(reg["Arah sinyal"]) <= {"BUY", "SELL"}
            harian = pd.read_excel(out, sheet_name="Harian")
            assert "Ada 1H" not in harian.columns
            assert set(reg["TF"]) <= {"D", "W"}
            txt = study.telegram_text(summary, reg)
            assert "IHSG PATTERN STUDY" in txt and "episodes" in txt
    finally:
        data.download = old


def test_meter_sma():
    from macroflow.signals import meter
    d = DAILY["AAAA"]
    e = meter(d, Params())[0]
    s_ = meter(d, Params(meter_ma="SMA"))[0]
    assert len(e) == len(s_) and (e != s_).any()  # rumus berbeda menghasilkan skor berbeda


def test_final_upto_and_weeks():
    from macroflow import index_alert
    d = DAILY["IHSG"]  # berakhir Jumat 16 Okt 2026
    t = pd.Timestamp
    assert index_alert.final_upto(d, t("2026-10-16 15:00"), "IDX") == t("2026-10-15")
    assert index_alert.final_upto(d, t("2026-10-16 16:20"), "IDX") == t("2026-10-16")
    assert index_alert.final_upto(d, t("2026-10-17 05:15"), "US") == t("2026-10-16")
    assert index_alert.final_upto(d, t("2026-10-17 02:00"), "US") == t("2026-10-15")
    w = market.weekly_bars(d)
    assert w.index[-1] == t("2026-10-16") and (w.index.weekday == 4).all()
    # Kamis 15 Okt: minggu berjalan belum selesai
    thu = market.closed_weeks(market.weekly_bars(d[d.index <= t("2026-10-15")]), t("2026-10-15"),
                              t("2026-10-15 16:20"))
    assert thu.index[-1] == t("2026-10-09")
    fri = market.closed_weeks(w, t("2026-10-16"), t("2026-10-16 16:20"))
    assert fri.index[-1] == t("2026-10-16")


def test_index_alert_run():
    from macroflow import index_alert
    patch_data()
    notify.TV_SYMBOLS.clear()
    data.YAHOO_SYMBOLS.clear()
    old = data.download
    calls = []

    def dl(tickers, period, interval, **kw):
        calls.append((tuple(tickers), interval))
        return fake_download(tickers, period, interval)
    data.download = dl
    kw = main.market_kwargs(CFG["market_warning"])
    try:
        with tempfile.TemporaryDirectory() as tmp:
            state = main.State(Path(tmp) / "state.json")
            sent = []
            for mkt, now in (("IDX", NOW), ("US", datetime(2026, 10, 17, 5, 20, tzinfo=WIB))):
                tg = FakeTelegram()
                index_alert.run(CFG, now, tg, state, mkt, Params(), kw)
                sent += tg.sent
            assert all(i == "1d" for _, i in calls)  # tanpa data 1H
            assert {t for c, _ in calls for t in c} == {"IHSG", "SP500"}
            n_keys = len(state.d["sent"])
            # sinyal yang sama tidak dikirim dua kali
            tg2 = FakeTelegram()
            index_alert.run(CFG, NOW, tg2, state, "IDX", Params(), kw)
            assert len(state.d["sent"]) == n_keys and not tg2.sent
            # paksa ada sinyal: hapus state lalu cek format pesan
            state.d["sent"].clear()
            state.d["done"].clear()
            tg3 = FakeTelegram()
            index_alert.run(CFG, NOW, tg3, state, "IDX", Params(), kw)
            msg = "\n".join(tg3.sent)
            if tg3.sent:
                assert "INDEX ALERT" in msg and "SMA50" in msg and "IHSG STATUS" in msg
                assert "1H" not in msg and "S&P 500" not in msg
            # sinyal hanya D / W, dan bukan ROYAL / GOLDEN
            for k in state.d["sent"]:
                _, code, tf, name, side, _ = k.split("|")
                assert tf in ("D", "W") and name in ("COMBO", "STANDAR", "METER")
    finally:
        data.download = old


def test_index_alert_messages_format():
    from macroflow import index_alert
    from macroflow.signals import Signal
    rows = [("D", Signal("COMBO", "SELL", TODAY, 6712.45, {"chg": -1.2, "rsi": 40, "stop": 6800.0})),
            ("D", Signal("METER", "SELL", TODAY, 6712.45, {"chg": -1.2, "rsi": 40, "stop": 6800.0})),
            ("W", Signal("STANDAR", "BUY", TODAY, 6712.45, {"chg": 0.5, "rsi": 55, "stop": 6500.0}))]
    lines = index_alert.fmt_signals(rows)
    assert len(lines) == 2 and "🔷COMBO + 📶STRONG" in lines[0] and "Weekly" in lines[1]
    assert "6,712.45" in lines[0]


def test_heikin_ashi():
    df = pd.DataFrame({"Open": [10.0, 12.0, 11.0], "High": [13.0, 14.0, 12.0],
                       "Low": [9.0, 11.0, 8.0], "Close": [12.0, 13.0, 9.0], "Volume": [1, 2, 3]})
    ha = ta.heikin_ashi(df)
    assert ha["Close"].tolist() == [11.0, 12.5, 10.0]          # (O+H+L+C)/4
    assert ha["Open"].tolist() == [11.0, 11.0, 11.75]          # (O+C)/2, lalu rata2 HA sebelumnya
    assert ha["High"].tolist() == [13.0, 14.0, 12.0] and ha["Low"].tolist() == [9.0, 11.0, 8.0]
    assert ha["Volume"].tolist() == [1, 2, 3] and df["Close"].tolist() == [12.0, 13.0, 9.0]


def test_signals_use_heikin_ashi_but_real_prices():
    d, h = DAILY["AAAA"], HOURLY["AAAA"]
    ha = evaluate(d, d, h, "D", Params(), last_n=150)
    plain = evaluate(d, d, h, "D", Params(heikin_ashi=False), last_n=150)
    key = lambda sigs: {(s.name, s.side, s.bar_time) for s in sigs}
    assert key(ha) != key(plain), "Heikin Ashi harus mengubah sinyal"
    for s in ha:  # harga & SL/TP tetap dari harga asli
        assert s.close == d.loc[s.bar_time, "Close"]
        if "sl" in s.info:
            assert s.info["sl"] < s.close
    p = main.params_from({"params": {"heikin_ashi": "false"}})
    assert p.heikin_ashi is False and main.params_from({}).heikin_ashi is True


def test_a_plus_setup():
    from macroflow.signals import Signal
    notify.TV_SYMBOLS.clear()
    info = {"chg": 1.0, "rsi": 60, "vol_ratio": 2.0, "sl": 950, "tp1": 1100, "tp2": 1150}
    items = [("AAAA", Signal("CHOCH", "BUY", TODAY, 1000.0, dict(info, smc_level="internal"))),
             ("AAAA", Signal("COMBO", "BUY", TODAY, 1000.0, dict(info))),
             ("BBBB", Signal("METER", "BUY", TODAY, 500.0, dict(info))),
             ("CCCC", Signal("CHOCH", "SELL", TODAY, 800.0, {"stop": 820})),
             ("DDDD", Signal("METER", "SELL", TODAY, 800.0, {"stop": 820}))]
    msg = "\n".join(notify.build_messages("H", items))
    assert "⭐ <b>A+ BUY SETUP</b>" in msg and "(1 stocks)" in msg
    assert msg.index("A+ BUY SETUP") < msg.index("BUY (other)") and msg.count(">AAAA<") == 1
    assert msg.index(">AAAA<") < msg.index("BUY (other)") < msg.index(">BBBB<")
    assert notify.is_a_plus("BUY", [items[0][1], items[2][1]]) and not notify.is_a_plus("SELL", [items[0][1]])
    only = "\n".join(notify.build_messages("H", items[2:3]))
    assert "A+" not in only.split("<i>")[0] and "(other)" not in only


def test_prepare_short_hourly_when_daily_only():
    patch_data()
    calls = []
    old = data.download

    def dl(tickers, period, interval, **kw):
        calls.append((interval, period))
        return fake_download(tickers, period, interval)
    data.download = dl
    try:
        cfg = dict(CFG, signals={"D": ["COMBO", "METER"], "1H": ["COMBO"]})
        main.prepare(cfg, NOW)
        assert ("1h", "5d") in calls
        calls.clear()
        main.prepare(dict(cfg, hourly_alerts=True), NOW)
        assert ("1h", "1y") in calls
    finally:
        data.download = old


def test_alerts_in_english():
    """Semua teks Telegram dalam bahasa Inggris (permintaan pengguna 23 Okt 2026)."""
    import re
    from macroflow import index_alert
    from macroflow.signals import Signal
    notify.TV_SYMBOLS.clear()
    h = make_crash_index()
    st = market.ihsg_status(daily_from_hourly(h), pd.Timestamp("2026-10-16 16:20"), Params())
    info = {"chg": 1.0, "rsi": 60, "vol_ratio": 2.0, "sl": 950, "tp1": 1100, "tp2": 1150, "stop": 900}
    sig = [("AAAA", Signal("STANDAR", "BUY", TODAY, 1000.0, dict(info)))]
    texts = [st.text(), st.warning_text(), "\n".join(notify.build_messages("H", sig)),
             "\n".join(index_alert.fmt_signals([("W", sig[0][1])]))]
    indo = re.compile(r"\b(sinyal|saham|harga|hari|minggu|naik|turun|peringatan|keras|sejak|dalam|"
                      r"terakhir|lanjutan|sementara|batal|di atas|di bawah|rezim|bursa|STANDAR)\b", re.I)
    for t in texts:
        assert not indo.search(t), indo.search(t).group(0)


def _zigzag(points, steps=6):
    """Harga zigzag lewat titik-titik: candle naik/turun bertahap, tanpa noise."""
    c = []
    for a, b in zip(points[:-1], points[1:]):
        c += list(np.linspace(a, b, steps + 1)[1:])
    c = np.array([points[0]] + c, float)
    o = np.concatenate([[c[0]], c[:-1]])
    return o, np.maximum(o, c) + 0.2, np.minimum(o, c) - 0.2, c


def test_smc_structure():
    """Struktur swing LuxAlgo: higher high berulang = BOS bullish, patahan pertama ke bawah = CHoCH."""
    from macroflow import smc
    o, h, l, c = _zigzag([100, 92, 100, 110, 104, 116, 108, 124, 115, 128, 118, 95, 105, 90], steps=6)
    r = smc.detect(o, h, l, c, swing=6, internal=3)
    bos_up = np.where(r.bos_buy)[0]
    first_sell = int(np.argmax(r.choch_sell))
    assert len(bos_up) >= 2 and r.choch_sell.any() and bos_up.max() < first_sell
    assert not r.bos_sell[:first_sell].any() and r.bos_sell[first_sell + 1:].any()
    assert r.levels[("CHOCH", "SELL", first_sell)] == "swing"
    assert not (r.bos_buy & r.bos_sell).any() and not (r.choch_buy & r.choch_sell).any()


def test_smc_internal_and_order_blocks():
    """Data acak: struktur internal muncul, dan setiap alert OB = zona benar-benar ditembus."""
    from macroflow import smc
    for df in (ta.heikin_ashi(HOURLY["BBBB"]), ta.heikin_ashi(DAILY["AAAA"])):
        o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
        r = smc.detect(o, h, l, c)
        assert "internal" in r.levels.values()
        assert r.ob_buy.any() or r.ob_sell.any()
        for j in np.where(r.ob_sell)[0]:   # demand (bullish OB) jebol ke bawah
            assert l[j] < r.zones[("OB", "SELL", j)][0]
        for j in np.where(r.ob_buy)[0]:    # supply (bearish OB) jebol ke atas
            assert h[j] > r.zones[("OB", "BUY", j)][1]
        # struktur internal tidak muncul sebelum ada pivot swing (extraCondition LuxAlgo)
        first_internal = min(j for (_, _, j), v in r.levels.items() if "internal" in v)
        assert first_internal > 50


def test_smc_no_repaint():
    """Sinyal SMC di candle i tidak berubah kalau data sesudah i ditambahkan."""
    from macroflow import smc
    d = ta.heikin_ashi(HOURLY["AAAA"])
    o, h, l, c = (d[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    full = smc.detect(o, h, l, c)
    for cut in (400, 900, 1300):
        part = smc.detect(o[:cut], h[:cut], l[:cut], c[:cut])
        for name in ("choch_buy", "choch_sell", "bos_buy", "bos_sell", "ob_buy", "ob_sell",
                     "fvg_buy", "fvg_sell"):
            assert (getattr(part, name) == getattr(full, name)[:cut]).all(), name


def test_smc_fvg():
    from macroflow import smc
    n = 40
    c = np.full(n, 100.0); o = c.copy(); h = c + 0.5; l = c - 0.5
    o[30], c[30], h[30], l[30] = 100, 110, 110.5, 99.8
    o[31], c[31], h[31], l[31] = 110, 111, 111.5, 109
    r = smc.detect(o, h, l, c, swing=10, internal=3)
    assert r.fvg_buy[31] and r.zones[("FVG", "BUY", 31)] == (100.5, 109)
    assert r.fvg_buy.sum() == 1 and not r.fvg_sell.any()


def test_smc_in_messages():
    from macroflow.signals import Signal
    notify.TV_SYMBOLS.clear()
    info = {"chg": 1.0, "rsi": 60, "vol_ratio": 2.0, "sl": 950, "tp1": 1100, "tp2": 1150}
    items = [("AAAA", Signal("CHOCH", "BUY", TODAY, 1000.0, dict(info, smc_level="swing"))),
             ("AAAA", Signal("OB", "BUY", TODAY, 1000.0, dict(info, zone=(960.0, 985.0))))]
    msg = "\n".join(notify.build_messages("H", items))
    assert "🔄CHoCH(swing) + 🧱OB (OB zone 960–985)" in msg and "change of character" in msg
    assert "LuxAlgo" in msg
    d, h = DAILY["BBBB"], HOURLY["BBBB"]
    names = {s.name for s in evaluate(h, d, h, "1H", Params(), last_n=len(h) - 1)}
    assert {"CHOCH", "BOS", "OB"} <= names


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
