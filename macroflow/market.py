"""Status pasar (IHSG) untuk ditampilkan di atas setiap notifikasi + peringatan keras.

Rezim EMA200 (dari pengamatan chart IHSG 2025–2026):
  - harga di atas EMA200 yang naik  -> UPTREND  (sinyal SELL tunggal sering jebakan)
  - harga di bawah EMA200 yang turun -> DOWNTREND (sinyal BUY biasanya hanya pantulan)
  - selain itu                         -> TRANSISI

Peringatan keras: IHSG mendapat sinyal SELL (jenis di `signals`, default COMBO/STANDAR/METER)
di minimal `min_events` candle berbeda dalam `window_days` hari bursa terakhir, dihitung dari
timeframe di `timeframes` (default Daily & 1H). Beberapa sinyal di candle yang sama = 1 kejadian.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ta
from .notify import SHORT, TV_SYMBOLS, esc, fmt_price
from .signals import Params, evaluate, meter

HARI = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
LINE = "━━━━━━━━━━━━━━━━━━"
DEFAULT_SIGNALS = ("COMBO", "STANDAR", "METER")
SLOPE_BARS = 20  # arah EMA200 = perubahan EMA200 dalam 20 hari bursa


def tgl(ts, with_time=False):
    s = f"{HARI[ts.weekday()]} {ts.day} {BULAN[ts.month - 1]}"
    return s + (f" {ts:%H:%M}" if with_time else "")


@dataclass
class Event:
    time: pd.Timestamp
    tf: str              # "D" / "1H"
    side: str            # "BUY" / "SELL"
    names: list          # jenis sinyal di candle ini
    partial: bool = False

    def label(self):
        names = " + ".join(SHORT.get(n, n) for n in self.names)
        note = " — belum close" if self.partial else ""
        return f"{tgl(self.time, self.tf == '1H')} ({'Daily' if self.tf == 'D' else '1H'}{note}): {names}"


def regime(close, ema200_now, ema200_prev):
    up = ema200_now > ema200_prev
    if close > ema200_now and up:
        return "UPTREND"
    if close < ema200_now and not up:
        return "DOWNTREND"
    return "TRANSISI"


def signal_events(daily, hclosed, p: Params, since, side="SELL", names=DEFAULT_SIGNALS,
                  tfs=("D", "1H"), now=None):
    """Kejadian sinyal (1 per candle per timeframe) sejak `since`."""
    names = set(names)
    out = []
    for tf in tfs:
        chart = daily if tf == "D" else hclosed
        if chart is None or len(chart) < 30:
            continue
        n_bars = int((chart.index >= since).sum())
        if n_bars == 0:
            continue
        groups = {}
        for s in evaluate(chart, daily, hclosed, tf, p, last_n=n_bars):
            if s.side == side and s.name in names and s.bar_time >= since:
                groups.setdefault(s.bar_time, []).append(s.name)
        for t, ns in groups.items():
            partial = (tf == "D" and now is not None and t >= now.normalize() and now.hour < 16)
            ns = sorted(set(ns), key=["ROYAL", "COMBO", "GOLDEN", "STANDAR", "METER"].index)
            out.append(Event(time=t, tf=tf, side=side, names=ns, partial=partial))
    # urutkan waktu; untuk tanggal yang sama, Daily dianggap di akhir hari
    out.sort(key=lambda e: e.time + (pd.Timedelta(hours=23) if e.tf == "D" else pd.Timedelta(0)))
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
    meter_h: str
    above_ema50: bool
    above_ema200: bool
    sells: list = field(default_factory=list)   # [Event] SELL dalam jendela
    buys: list = field(default_factory=list)    # [Event] BUY dalam jendela
    window_days: int = 5
    min_events: int = 2

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
            f'📊 <b>STATUS {esc(self.code)}: {emoji} {self.trend}</b>',
            f'<a href="{url}">{esc(self.code)}</a> {fmt_price(self.close)}{chg} · '
            f'{"di atas" if self.above_ema50 else "di bawah"} EMA50',
            self._regime_line(),
            f"Illu Trx D: <b>{illu}</b> sejak {tgl(self.illu_since)} · "
            f"Meter D: <b>{self.meter_d}</b> · 1H: <b>{self.meter_h}</b>",
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
            out = (f"🚨🚨🚨 <b>PERINGATAN KERAS: {len(self.sells)}× sinyal SELL {esc(self.code)} "
                   f"dalam {self.window_days} hari bursa{extra}</b> "
                   f"(terakhir {tgl(self.sells[-1].time, self.sells[-1].tf == '1H')}) "
                   "— hati-hati BUY baru 🚨🚨🚨\n" + out)
        return out

    def warning_text(self):
        lines = [
            "🚨🚨🚨 <b>PERINGATAN KERAS — " + esc(self.code) + "</b> 🚨🚨🚨",
            f"<b>{len(self.sells)}× sinyal SELL dalam {self.window_days} hari bursa terakhir</b>",
            *[f"• {e.label()}" for e in self.sells],
            self._regime_line(),
        ]
        if self.regime == "DOWNTREND":
            lines.append("‼️ Kumpulan sinyal SELL di bawah EMA200 yang turun — di chart IHSG 2026 pola ini "
                         "berulang kali diikuti penurunan lanjutan.")
        lines.append("‼️ Tekanan jual pasar kuat. Pertimbangkan menahan BUY baru, "
                     "memperketat stop, atau mengurangi posisi.")
        return "\n".join(lines)


def ihsg_status(daily, hourly, now, p: Params, code="IHSG", window_days=5, min_events=2,
                signals=DEFAULT_SIGNALS, timeframes=("D", "1H")):
    """daily: OHLCV harian (bar hari ini boleh parsial); hourly: OHLCV 1 jam; now: Timestamp naive WIB."""
    if daily is None or len(daily) < 60:
        return None
    hclosed = None
    if hourly is not None and len(hourly):
        hclosed = hourly[hourly.index + pd.Timedelta(hours=1) <= now]
    c = daily["Close"].to_numpy(float)
    _, pos = ta.illu_trx(c, daily["High"].to_numpy(float), daily["Low"].to_numpy(float),
                         p.ut_key, p.ut_period)
    flips = np.where(pos != np.concatenate([[pos[0]], pos[:-1]]))[0]
    since = daily.index[flips[-1]] if len(flips) else daily.index[0]
    _, mtxt_d = meter(daily, p)
    mtxt_h = meter(hclosed, p)[1] if hclosed is not None and len(hclosed) > 60 else ["-"]
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
    kw = dict(names=signals, tfs=tuple(timeframes), now=now)
    return MarketStatus(
        code=code, close=c[-1], chg=(c[-1] / c[-2] - 1) * 100 if c[-2] else np.nan,
        trend=trend, regime=regime(c[-1], e200[-1], prev), ema200_slope=slope,
        illu_pos=int(pos[-1]), illu_since=since, meter_d=md, meter_h=str(mtxt_h[-1]),
        above_ema50=bool(c[-1] > ema50), above_ema200=bool(c[-1] > e200[-1]),
        sells=signal_events(daily, hclosed, p, start, "SELL", **kw),
        buys=signal_events(daily, hclosed, p, start, "BUY", **kw),
        window_days=window_days, min_events=min_events)
