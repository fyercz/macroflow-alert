"""Status pasar (IHSG) untuk ditampilkan di atas setiap notifikasi + peringatan keras.

Rezim EMA200 (dari pengamatan chart IHSG 2025–2026):
  - harga di atas EMA200 yang naik  -> UPTREND  (sinyal SELL tunggal sering jebakan)
  - harga di bawah EMA200 yang turun -> DOWNTREND (sinyal BUY biasanya hanya pantulan)
  - selain itu                         -> TRANSISI

Peringatan keras: IHSG mendapat sinyal SELL (jenis di `signals`, default COMBO/STANDAR/METER)
di minimal `min_events` candle berbeda dalam `window_days` hari bursa terakhir, dihitung dari
timeframe di `timeframes` (default Daily & Weekly). Beberapa sinyal di candle yang sama = 1 kejadian.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ta
from .notify import SHORT, TV_SYMBOLS, esc
from .signals import Params, candles, evaluate, meter

HARI = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
LINE = "━━━━━━━━━━━━━━━━━━"
DEFAULT_SIGNALS = ("COMBO", "STANDAR", "METER")
SLOPE_BARS = 20  # arah EMA200 = perubahan EMA200 dalam 20 hari bursa


def fmt_index(x):
    """Angka indeks gaya Indonesia: 6.712,45"""
    if x is None or np.isnan(x):
        return "-"
    return f"{x:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def tgl(ts, with_time=False):
    s = f"{HARI[ts.weekday()]} {ts.day} {BULAN[ts.month - 1]}"
    return s + (f" {ts:%H:%M}" if with_time else "")


@dataclass
class Event:
    time: pd.Timestamp
    tf: str              # "D" / "W"
    side: str            # "BUY" / "SELL"
    names: list          # jenis sinyal di candle ini
    partial: bool = False

    def label(self):
        names = " + ".join(SHORT.get(n, n) for n in self.names)
        note = " — belum close" if self.partial else ""
        tf = "Daily" if self.tf == "D" else "Weekly"
        return f"{tgl(self.time)} ({tf}{note}): {names}"


def regime(close, ema200_now, ema200_prev):
    up = ema200_now > ema200_prev
    if close > ema200_now and up:
        return "UPTREND"
    if close < ema200_now and not up:
        return "DOWNTREND"
    return "TRANSISI"


def weekly_bars(daily):
    """Bar mingguan dari data harian. Index = tanggal bursa terakhir minggu itu."""
    if daily is None or not len(daily):
        return daily
    key = daily.index.normalize() - pd.to_timedelta(daily.index.weekday, unit="D")
    g = daily.groupby(key, sort=True)
    w = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                      "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    w.index = pd.DatetimeIndex(g.apply(lambda x: x.index.max()).values)
    return w


def closed_weeks(weekly, final_upto, today):
    """Hanya minggu yang sudah selesai: hari terakhirnya sudah final DAN (Jumat, atau sudah lewat minggunya)."""
    if weekly is None or not len(weekly):
        return weekly
    idx = weekly.index
    week_end = idx.normalize() - pd.to_timedelta(idx.weekday, unit="D") + pd.Timedelta(days=4)
    ok = (idx <= final_upto) & ((idx.weekday == 4) | (week_end < today.normalize()))
    return weekly[ok]


def signal_events(daily, weekly, p: Params, since, side="SELL", names=DEFAULT_SIGNALS,
                  tfs=("D", "W"), final_upto=None):
    """Kejadian sinyal (1 per candle per timeframe) sejak `since`.

    daily  : bar harian (bar terakhir boleh belum final -> ditandai `partial`)
    weekly : bar mingguan yang sudah selesai (lihat closed_weeks)
    """
    names = set(names)
    out = []
    for tf in tfs:
        chart = daily if tf == "D" else weekly
        if chart is None or len(chart) < 30:
            continue
        n_bars = int((chart.index >= since).sum())
        if n_bars == 0:
            continue
        groups = {}
        for s in evaluate(chart, daily, None, tf, p, last_n=n_bars):
            if s.side == side and s.name in names and s.bar_time >= since:
                groups.setdefault(s.bar_time, []).append(s.name)
        for t, ns in groups.items():
            partial = bool(tf == "D" and final_upto is not None and t > final_upto)
            ns = sorted(set(ns), key=["ROYAL", "COMBO", "GOLDEN", "STANDAR", "METER"].index)
            out.append(Event(time=t, tf=tf, side=side, names=ns, partial=partial))
    # urutkan waktu; untuk tanggal yang sama, Weekly sesudah Daily
    out.sort(key=lambda e: (e.time, e.tf == "W"))
    return out


@dataclass
class MarketStatus:
    code: str
    close: float
    chg: float
    trend: str                 # NAIK / TURUN / CAMPURAN
    regime: str                # UPTREND / DOWNTREND / TRANSISI
    ema200_slope: float        # % perubahan EMA200 dalam SLOPE_BARS hari
    illu_pos: int
    illu_since: pd.Timestamp
    meter_d: str
    meter_w: str
    above_ema50: bool
    above_ema200: bool
    sells: list = field(default_factory=list)   # [Event] SELL dalam jendela
    buys: list = field(default_factory=list)    # [Event] BUY dalam jendela
    window_days: int = 5
    min_events: int = 2
    name: str = ""

    @property
    def warning(self):
        return len(self.sells) >= self.min_events

    @property
    def buy_confirm(self):
        """Pola rally: >= min_events candle BUY dalam jendela, di atas EMA200 yang naik."""
        return self.regime == "UPTREND" and len(self.buys) >= self.min_events

    def _regime_line(self):
        arrow = "↑ naik" if self.ema200_slope > 0 else "↓ turun"
        pos = "di atas" if self.above_ema200 else "di bawah"
        emoji = {"UPTREND": "🟢", "DOWNTREND": "🔴"}.get(self.regime, "🟡")
        note = {"UPTREND": "SELL tunggal sering jebakan",
                "DOWNTREND": "BUY biasanya hanya pantulan",
                "TRANSISI": "arah besar belum jelas"}[self.regime]
        return (f"Rezim: {emoji} <b>{self.regime}</b> — harga {pos} EMA200, EMA200 {arrow} "
                f"({self.ema200_slope:+.1f}%/{SLOPE_BARS} hari) · <i>{note}</i>")

    def text(self):
        emoji = {"NAIK": "🟢", "TURUN": "🔴"}.get(self.trend, "🟡")
        url = f"https://www.tradingview.com/chart/?symbol={TV_SYMBOLS.get(self.code, 'IDX:' + self.code)}"
        chg = "" if np.isnan(self.chg) else f" ({self.chg:+.1f}%)"
        illu = "BUY" if self.illu_pos == 1 else "SELL"
        lines = [
            LINE,
            f'📊 <b>STATUS {esc(self.name)}: {emoji} {self.trend}</b>',
            f'<a href="{url}">{esc(self.name)}</a> {fmt_index(self.close)}{chg} · '
            f'{"di atas" if self.above_ema50 else "di bawah"} EMA50',
            self._regime_line(),
            f"Illu Trx D: <b>{illu}</b> sejak {tgl(self.illu_since)} · "
            f"Meter D: <b>{self.meter_d}</b> · W: <b>{self.meter_w}</b>",
            f"Sinyal {self.window_days} hari bursa: 🔴 SELL {len(self.sells)}× · 🟢 BUY {len(self.buys)}×",
        ]
        if self.sells and not self.warning:
            left = self.min_events - len(self.sells)
            lines.append(f"⚠️ Sudah {len(self.sells)}× sinyal SELL — {left} lagi = peringatan keras")
        if self.buy_confirm:
            lines.append(f"✅ <b>Konfirmasi tren naik</b>: {len(self.buys)}× sinyal BUY di atas EMA200 yang naik")
        lines.append(LINE)
        out = "\n".join(lines)
        if self.warning:  # versi ringkas; pesan lengkap dikirim terpisah 1x/hari
            extra = " di bawah EMA200 yang turun" if self.regime == "DOWNTREND" else ""
            out = (f"🚨🚨🚨 <b>PERINGATAN KERAS: {len(self.sells)}× sinyal SELL {esc(self.name)} "
                   f"dalam {self.window_days} hari bursa{extra}</b> "
                   f"(terakhir {tgl(self.sells[-1].time)}) "
                   "— hati-hati BUY baru 🚨🚨🚨\n" + out)
        return out

    def warning_text(self):
        lines = [
            "🚨🚨🚨 <b>PERINGATAN KERAS — " + esc(self.name) + "</b> 🚨🚨🚨",
            f"<b>{len(self.sells)}× sinyal SELL dalam {self.window_days} hari bursa terakhir</b>",
            *[f"• {e.label()}" for e in self.sells],
            self._regime_line(),
        ]
        if self.regime == "DOWNTREND":
            lines.append("‼️ Kumpulan sinyal SELL di bawah EMA200 yang turun — pada chart IHSG 2026 pola ini "
                         "berulang kali diikuti penurunan lanjutan.")
        lines.append("‼️ Tekanan jual pasar kuat. Pertimbangkan menahan BUY baru, "
                     "memperketat stop, atau mengurangi posisi.")
        return "\n".join(lines)


def ihsg_status(daily, now, p: Params, code="IHSG", window_days=5, min_events=2,
                signals=DEFAULT_SIGNALS, timeframes=("D", "W"), final_upto=None, name=None):
    """Status indeks dari data harian (bar terakhir boleh belum final).

    final_upto: tanggal bar harian terakhir yang sudah final (default: semua final).
    now: Timestamp naive (WIB).
    """
    if daily is None or len(daily) < 60:
        return None
    final_upto = daily.index[-1] if final_upto is None else final_upto
    real_c = daily["Close"].to_numpy(float)          # harga asli untuk ditampilkan
    src = candles(daily, p)                          # indikator dari Heikin Ashi (sesuai setelan)
    c = src["Close"].to_numpy(float)
    _, pos = ta.illu_trx(c, src["High"].to_numpy(float), src["Low"].to_numpy(float),
                         p.ut_key, p.ut_period)
    flips = np.where(pos != np.concatenate([[pos[0]], pos[:-1]]))[0]
    since = daily.index[flips[-1]] if len(flips) else daily.index[0]
    _, mtxt_d = meter(daily, p)
    weekly = weekly_bars(daily)
    mtxt_w = meter(weekly, p)[1] if len(weekly) > 2 else ["-"]
    ema50 = ta.ema(c, 50)[-1]
    e200 = ta.ema(c, 200)
    if np.isnan(e200[-1]):  # histori < 200 bar
        e200 = ta.ema(c, min(len(c) - 1, 100))
    prev = e200[-1 - SLOPE_BARS] if len(e200) > SLOPE_BARS else e200[0]
    slope = (e200[-1] / prev - 1) * 100 if prev and not np.isnan(prev) else 0.0
    md = str(mtxt_d[-1])
    if pos[-1] == 1 and md in ("BUY", "STRONG BUY"):
        trend = "NAIK"
    elif pos[-1] == -1 and md in ("SELL", "STRONG SELL"):
        trend = "TURUN"
    else:
        trend = "CAMPURAN"

    days = daily.index.normalize().unique()
    start = days[-window_days] if len(days) >= window_days else days[0]
    wk = closed_weeks(weekly, final_upto, now)
    kw = dict(names=signals, tfs=tuple(timeframes), final_upto=final_upto)
    return MarketStatus(
        code=code, name=name or code, close=real_c[-1],
        chg=(real_c[-1] / real_c[-2] - 1) * 100 if real_c[-2] else np.nan,
        trend=trend, regime=regime(c[-1], e200[-1], prev), ema200_slope=slope,
        illu_pos=int(pos[-1]), illu_since=since, meter_d=md, meter_w=str(mtxt_w[-1]),
        above_ema50=bool(c[-1] > ema50), above_ema200=bool(c[-1] > e200[-1]),
        sells=signal_events(daily, wk, p, start, "SELL", **kw),
        buys=signal_events(daily, wk, p, start, "BUY", **kw),
        window_days=window_days, min_events=min_events)
