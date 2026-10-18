"""Status pasar (IHSG) untuk ditampilkan di atas setiap notifikasi + peringatan keras.

Peringatan keras: IHSG mendapat sinyal STRONG SELL (METER berubah menjadi STRONG SELL)
minimal `min_strong_sell` kali dalam `window_days` hari bursa terakhir, dihitung dari
timeframe Daily dan 1H.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ta
from .notify import TV_SYMBOLS, esc, fmt_price
from .signals import Params, meter

HARI = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
LINE = "━━━━━━━━━━━━━━━━━━"


def tgl(ts, with_time=False):
    s = f"{HARI[ts.weekday()]} {ts.day} {BULAN[ts.month - 1]}"
    return s + (f" {ts:%H:%M}" if with_time else "")


@dataclass
class MarketStatus:
    code: str
    close: float
    chg: float
    trend: str                 # NAIK / TURUN / CAMPURAN
    illu_pos: int              # 1 / -1
    illu_since: pd.Timestamp
    meter_d: str
    meter_h: str
    above_ema50: bool
    above_ema200: bool
    events: list = field(default_factory=list)   # [(waktu, "D"/"1H", parsial?)]
    window_days: int = 5
    min_strong_sell: int = 2

    @property
    def warning(self):
        return len(self.events) >= self.min_strong_sell

    def text(self):
        emoji = {"NAIK": "🟢", "TURUN": "🔴"}.get(self.trend, "🟡")
        url = f"https://www.tradingview.com/chart/?symbol={TV_SYMBOLS.get(self.code, 'IDX:' + self.code)}"
        chg = "" if np.isnan(self.chg) else f" ({self.chg:+.1f}%)"
        illu = "BUY" if self.illu_pos == 1 else "SELL"
        ema = []
        ema.append(("di atas" if self.above_ema50 else "di bawah") + " EMA50")
        ema.append(("di atas" if self.above_ema200 else "di bawah") + " EMA200")
        lines = [
            LINE,
            f'📊 <b>STATUS {esc(self.code)}: {emoji} {self.trend}</b>',
            f'<a href="{url}">{esc(self.code)}</a> {fmt_price(self.close)}{chg} · {", ".join(ema)}',
            f"Illu Trx D: <b>{illu}</b> sejak {tgl(self.illu_since)} · "
            f"Meter D: <b>{self.meter_d}</b> · 1H: <b>{self.meter_h}</b>",
        ]
        if self.events and not self.warning:
            lines.append(f"⚠️ {len(self.events)}× STRONG SELL dalam {self.window_days} hari bursa terakhir")
        lines.append(LINE)
        out = "\n".join(lines)
        if self.warning:  # versi ringkas; pesan lengkap dikirim terpisah 1x/hari
            last = self.events[-1]
            out = (f"🚨🚨🚨 <b>PERINGATAN KERAS: {len(self.events)}× STRONG SELL {esc(self.code)} "
                   f"dalam {self.window_days} hari bursa</b> (terakhir {tgl(last[0], last[1] == '1H')}) "
                   "— hati-hati BUY baru 🚨🚨🚨\n" + out)
        return out

    def warning_text(self):
        rows = []
        for t, tf, partial in self.events:
            label = tgl(t, with_time=(tf == "1H"))
            note = " — belum close" if partial else ""
            rows.append(f"• {label} ({'Daily' if tf == 'D' else '1H'}{note})")
        return "\n".join([
            "🚨🚨🚨 <b>PERINGATAN KERAS — " + esc(self.code) + "</b> 🚨🚨🚨",
            f"<b>{len(self.events)}× sinyal STRONG SELL dalam {self.window_days} hari bursa terakhir</b>",
            *rows,
            "‼️ Tekanan jual pasar sangat kuat. Pertimbangkan menahan BUY baru, "
            "memperketat stop, atau mengurangi posisi.",
        ])


def _strong_sell_events(df, since, p):
    """Waktu bar saat meter BERUBAH menjadi STRONG SELL (sama dengan sinyal METER SELL)."""
    if df is None or len(df) < 2:
        return []
    _, txt = meter(df, p)
    prev = np.concatenate([[""], txt[:-1]])
    hit = (txt == "STRONG SELL") & (prev != "STRONG SELL")
    return [t for t, h in zip(df.index, hit) if h and t >= since]


def ihsg_status(daily, hourly, now, p: Params, code="IHSG", window_days=5, min_strong_sell=2):
    """daily: OHLCV harian (bar hari ini boleh parsial); hourly: OHLCV 1 jam; now: Timestamp naive WIB."""
    if daily is None or len(daily) < 60:
        return None
    hclosed = None
    if hourly is not None and len(hourly):
        hclosed = hourly[hourly.index + pd.Timedelta(hours=1) <= now]
    c = daily["Close"].to_numpy(float)
    h = daily["High"].to_numpy(float)
    l = daily["Low"].to_numpy(float)
    _, pos = ta.illu_trx(c, h, l, p.ut_key, p.ut_period)
    flips = np.where(pos != np.concatenate([[pos[0]], pos[:-1]]))[0]
    since = daily.index[flips[-1]] if len(flips) else daily.index[0]
    _, mtxt_d = meter(daily, p)
    mtxt_h = meter(hclosed, p)[1] if hclosed is not None and len(hclosed) > 60 else ["-"]
    ema50 = ta.ema(c, 50)[-1]
    ema200 = ta.ema(c, 200)[-1]
    md = str(mtxt_d[-1])
    if pos[-1] == 1 and md in ("BUY", "STRONG BUY"):
        trend = "NAIK"
    elif pos[-1] == -1 and md in ("SELL", "STRONG SELL"):
        trend = "TURUN"
    else:
        trend = "CAMPURAN"

    days = daily.index.normalize().unique()
    start = days[-window_days] if len(days) >= window_days else days[0]
    today = now.normalize()
    events = [(t, "D", t >= today and now.hour < 16) for t in _strong_sell_events(daily, start, p)]
    events += [(t, "1H", False) for t in _strong_sell_events(hclosed, start, p)]
    events.sort(key=lambda e: e[0])

    return MarketStatus(
        code=code, close=c[-1], chg=(c[-1] / c[-2] - 1) * 100 if c[-2] else np.nan,
        trend=trend, illu_pos=int(pos[-1]), illu_since=since, meter_d=md,
        meter_h=str(mtxt_h[-1]), above_ema50=bool(c[-1] > ema50),
        above_ema200=bool(c[-1] > ema200), events=events,
        window_days=window_days, min_strong_sell=min_strong_sell)
