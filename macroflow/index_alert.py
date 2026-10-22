"""Alert indeks (IHSG, S&P 500, ...) — hanya timeframe Daily & Weekly.

- Meter STRONG BUY/SELL memakai SMA50 (config `index_alert.meter_ma`), sinyal lain sama
  dengan saham (COMBO, STANDAR/Illu Trx). ROYAL & GOLDEN tidak dipakai untuk indeks.
- Hanya candle yang sudah FINAL:
    IDX : bar hari ini final setelah 16:00 WIB
    US  : bar tanggal kemarin (WIB) final setelah 05:00 WIB (bursa AS tutup 03:00/04:00 WIB)
  Candle Weekly dikirim setelah minggunya selesai (Jumat final, atau sudah lewat minggunya).
- Setiap sinyal dikirim sekali (state.json), plus status indeks & peringatan keras.
"""
import copy
import logging

import pandas as pd

from . import data, market, notify
from .notify import esc
from .signals import Params, evaluate

log = logging.getLogger("macroflow.index")

DEFAULT_INDICES = {
    "IHSG": {"yahoo": "^JKSE", "tradingview": "IDX:COMPOSITE", "name": "IHSG", "market": "IDX"},
    "SP500": {"yahoo": "^GSPC", "tradingview": "SP:SPX", "name": "S&P 500", "market": "US"},
}
MARKET_LABEL = {"IDX": "Indonesia", "US": "US market"}


def specs(cfg):
    ia = cfg.get("index_alert") or {}
    raw = ia.get("indices") or DEFAULT_INDICES
    out = []
    for code, sp in raw.items():
        code = str(code).upper()
        out.append({"code": code, "yahoo": sp["yahoo"],
                    "tradingview": sp.get("tradingview", f"IDX:{code}"),
                    "name": sp.get("name", code), "market": str(sp.get("market", "IDX")).upper()})
    return out


def register(cfg):
    """Daftarkan simbol Yahoo/TradingView indeks (untuk download, link, dan `check`)."""
    for sp in specs(cfg):
        data.YAHOO_SYMBOLS[sp["code"]] = sp["yahoo"]
        notify.TV_SYMBOLS[sp["code"]] = sp["tradingview"]


def index_params(cfg, base: Params):
    p = copy.copy(base)
    p.meter_ma = str((cfg.get("index_alert") or {}).get("meter_ma", "SMA")).upper()
    return p


def settings(cfg):
    ia = cfg.get("index_alert") or {}
    return (tuple(str(x).upper() for x in ia.get("signals", ["COMBO", "STANDAR", "METER", "CHOCH", "BOS", "OB", "FVG"])),
            tuple(str(x).upper() for x in ia.get("timeframes", ["D", "W"])))


def final_upto(daily, now, mkt):
    """Tanggal bar harian terakhir yang sudah final (None kalau tidak ada)."""
    today = now.normalize()
    if mkt == "US":
        cutoff = today if now.hour >= 5 else today - pd.Timedelta(days=1)
        ok = daily.index < cutoff
    else:
        ok = daily.index <= today if now.hour >= 16 else daily.index < today
    idx = daily.index[ok]
    return idx[-1] if len(idx) else None


def _novol(df):
    df = df.copy()
    df["Volume"] = 0.0  # ROYAL (butuh volume) tidak dipakai untuk indeks
    return df


def index_signals(d_final, weekly, p, names, tfs, last_n=2):
    """Sinyal di candle terakhir per timeframe (Daily: `last_n` candle supaya run yang terlewat
    tetap terkirim; Weekly: minggu terakhir yang sudah selesai). Return list (tf, Signal)."""
    out = []
    d0 = _novol(d_final)
    for tf in tfs:
        chart = d0 if tf == "D" else (_novol(weekly) if weekly is not None else None)
        if chart is None or len(chart) < 30:
            continue
        for s in evaluate(chart, d0, None, tf, p, last_n=last_n if tf == "D" else 1):
            if s.name in names:
                out.append((tf, s))
    return out


def sig_key(code, tf, s):
    return f"INDEX|{code}|{tf}|{s.name}|{s.side}|{s.bar_time:%Y-%m-%d}"


def fmt_signals(rows):
    """rows: list (tf, Signal) satu indeks -> baris teks, sinyal di candle sama digabung."""
    groups = {}
    for tf, s in rows:
        groups.setdefault((tf, s.bar_time, s.side), []).append(s)
    lines = []
    for (tf, bt, side), sigs in sorted(groups.items(), key=lambda g: (g[0][1], g[0][0] == "W")):
        sigs = sorted(sigs, key=lambda s: notify.ORDER.index(s.name))
        s0 = sigs[0]
        emoji = "⭐🟢" if notify.is_a_plus(side, sigs) else ("🟢" if side == "BUY" else "🔴")
        when = (f"Daily {market.tgl(bt)}" if tf == "D"
                else f"<b>Weekly</b> week ending {market.tgl(bt)}")
        names = " + ".join(notify.short_name(s) for s in sigs) + notify.zone_text(sigs, market.fmt_index)
        extra = [f"close {market.fmt_index(s0.close)}"]
        if s0.info.get("chg") == s0.info.get("chg"):  # bukan NaN
            extra.append(f"{s0.info['chg']:+.1f}%")
        if s0.info.get("rsi") == s0.info.get("rsi"):
            extra.append(f"RSI {s0.info['rsi']:.0f}")
        extra.append(f"Stop Illu {market.fmt_index(s0.info.get('stop'))}")
        lines.append(f"{emoji} <b>{side}</b> · {when} · <b>{names}</b>\n   " + " · ".join(extra))
    return lines


def run(cfg, now, tg, state, mkt, base_params, market_kwargs):
    """Kirim alert indeks untuk satu bursa ("IDX" / "US"). now: datetime WIB (aware)."""
    sp_list = [sp for sp in specs(cfg) if sp["market"] == mkt]
    if not sp_list:
        log.info("Tidak ada indeks untuk bursa %s", mkt)
        return
    register(cfg)
    p = index_params(cfg, base_params)
    names, tfs = settings(cfg)
    now_naive = pd.Timestamp(now.replace(tzinfo=None))
    day_str = f"{now_naive:%Y-%m-%d}"
    done = state.d.setdefault("done", {})
    codes = [sp["code"] for sp in sp_list]
    daily = data.download(codes, cfg.get("index_period", "5y"), "1d")

    blocks = []
    for sp in sp_list:
        code = sp["code"]
        d = daily.get(code)
        if d is None or len(d) < 60:
            log.warning("Data %s tidak tersedia", code)
            continue
        fu = final_upto(d, now_naive, mkt)
        if fu is None:
            continue
        d_fin = d[d.index <= fu]
        weekly = market.closed_weeks(market.weekly_bars(d_fin), fu, now_naive)
        rows = [(tf, s) for tf, s in index_signals(d_fin, weekly, p, names, tfs)
                if not state.seen(sig_key(code, tf, s))]
        for tf, s in rows:
            state.mark(sig_key(code, tf, s), day_str)
        status = market.ihsg_status(d_fin, now_naive, p, code=code, name=sp["name"],
                                    **market_kwargs)
        warn_new = bool(status and status.warning and not done.get(f"warn|{code}|{day_str}"))
        if warn_new:
            done[f"warn|{code}|{day_str}"] = True
        if not rows and not warn_new:
            log.info("%s: tidak ada sinyal baru", code)
            continue
        parts = []
        if warn_new:
            parts.append(status.warning_text())
        if status:
            parts.append(status.text())
        if rows:
            parts.append(f"🆕 <b>New {esc(sp['name'])} signals</b>\n" + "\n".join(fmt_signals(rows)))
        blocks.append("\n\n".join(parts))
        log.info("%s: %d sinyal baru%s", code, len(rows), " + peringatan" if warn_new else "")

    if blocks:
        header = (f"📈 <b>INDEX ALERT — {MARKET_LABEL.get(mkt, mkt)}</b> (Daily & Weekly)\n"
                  f"{market.tgl(now_naive)}, {now_naive.year} {now_naive:%H:%M} WIB · "
                  f"<i>STRONG = Meter using {p.meter_ma}50</i>")
        legend = (f"<i>📶STRONG = Meter STRONG BUY/SELL (price vs {p.meter_ma}50 + RSI14 + ADX14) · "
                  "🔷COMBO = Illu Trx + EMA50 + MACD + RSI · 📍STANDARD = Illu Trx reversal · "
                  + notify.SMC_LEGEND + " · Signals & zones from Heikin Ashi candles, prices are regular</i>")
        tg.send_all(notify.split_messages(header, blocks + [legend]))
    state.save(now_naive.normalize())
