"""MacroFlow V65 Alert — runner.

Contoh:
  python -m macroflow.main auto              # dipakai jadwal otomatis
  python -m macroflow.main run daily-final   # paksa mode tertentu
  python -m macroflow.main check BBCA --tf D # lihat riwayat sinyal 1 saham
  python -m macroflow.main test-telegram
  python -m macroflow.main chat-id
"""
import argparse
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yaml

from . import data, notify
from .signals import Params, evaluate

ROOT = Path(__file__).resolve().parent.parent
WIB = ZoneInfo("Asia/Jakarta")
log = logging.getLogger("macroflow")


# ----------------------------------------------------------------------
def load_config():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text()) or {}
    return cfg


def setup_indices(cfg):
    """Daftarkan indeks (mis. IHSG) dari config. Return {kode: set sinyal}."""
    out = {}
    for code, spec in (cfg.get("indices") or {}).items():
        code = str(code).upper()
        data.YAHOO_SYMBOLS[code] = spec["yahoo"]
        notify.TV_SYMBOLS[code] = spec.get("tradingview", f"IDX:{code}")
        out[code] = set(spec.get("signals") or ["COMBO", "GOLDEN", "STANDAR", "METER"])
    return out


def params_from(cfg):
    p = Params()
    for k, v in (cfg.get("params") or {}).items():
        if hasattr(p, k):
            setattr(p, k, type(getattr(p, k))(v))
    return p


class State:
    def __init__(self, path: Path):
        self.path = path
        self.d = {"sent": {}, "preclose": {}}
        if path.exists():
            try:
                self.d.update(json.loads(path.read_text()))
            except Exception:
                pass

    def seen(self, key):
        return key in self.d["sent"]

    def mark(self, key, day):
        self.d["sent"][key] = day

    def save(self, today):
        cutoff = (today - timedelta(days=14)).strftime("%Y-%m-%d")
        self.d["sent"] = {k: v for k, v in self.d["sent"].items() if v >= cutoff}
        self.d["preclose"] = {k: v for k, v in self.d["preclose"].items() if k >= cutoff}
        self.d["done"] = {k: v for k, v in self.d.get("done", {}).items()
                          if k.split("|")[-1] >= cutoff}
        self.path.write_text(json.dumps(self.d, indent=1))


def sig_key(ticker, tf, s):
    return f"{ticker}|{tf}|{s.name}|{s.side}|{s.bar_time:%Y-%m-%d %H:%M}"


# ----------------------------------------------------------------------
def liquid(df, min_value, min_price, today):
    hist = df[df.index < today].tail(20)
    if len(df) < 60 or hist.empty:
        return False
    value = (hist["Close"] * hist["Volume"]).mean()
    return value >= min_value and df["Close"].iloc[-1] >= min_price


def prepare(cfg, now):
    today = pd.Timestamp(now.date())
    tickers = data.load_universe(cfg, ROOT / "tickers.txt")
    log.info("Universe: %d saham", len(tickers))
    daily = data.download(tickers, cfg.get("daily_period", "3y"), "1d")
    flt = cfg.get("filter") or {}
    min_value = float(flt.get("min_avg_value", 2e9))
    min_price = float(flt.get("min_price", 50))
    keep = [t for t, df in daily.items() if liquid(df, min_value, min_price, today)]
    log.info("Lolos filter likuiditas: %d saham", len(keep))
    idx = list(data.YAHOO_SYMBOLS)  # indeks: selalu dicek, tanpa filter
    if idx:
        daily.update(data.download(idx, cfg.get("daily_period", "3y"), "1d"))
        keep = idx + [t for t in keep if t not in idx]
    hourly = data.download(keep, cfg.get("hourly_period", "1y"), "1h")
    return today, keep, daily, hourly


def market_open_today(hourly, today):
    for t in ("BBCA", "BBRI", "TLKM"):
        if t in hourly:
            return (hourly[t].index >= today).any()
    return any((df.index >= today).any() for df in list(hourly.values())[:20])


def enabled(cfg, tf):
    return set((cfg.get("signals") or {}).get(tf, ["ROYAL", "COMBO", "GOLDEN", "STANDAR", "METER"]))


def run_modes(modes, cfg, now, tg, state):
    p = params_from(cfg)
    index_sigs = setup_indices(cfg)
    today, keep, daily, hourly = prepare(cfg, now)
    if not market_open_today(hourly, today):
        log.info("Tidak ada data hari ini (libur bursa?) — selesai")
        return
    now_naive = pd.Timestamp(now.replace(tzinfo=None))
    day_str = today.strftime("%Y-%m-%d")

    done_days = state.d.setdefault("done", {})
    for mode in modes:
        if mode != "hourly" and done_days.get(f"{mode}|{day_str}"):
            log.info("%s hari ini sudah dikirim — lewati", mode)
            continue
        if mode != "hourly":
            done_days[f"{mode}|{day_str}"] = True
        tf = "1H" if mode == "hourly" else "D"
        allow = enabled(cfg, tf)
        found = []
        for t in keep:
            d = daily.get(t)
            h = hourly.get(t)
            if d is None or h is None:
                continue
            d = data.merge_today(d, h, today)
            if tf == "D":
                if d.index[-1] != today:
                    continue
                chart = d
            else:
                done = h[h.index + pd.Timedelta(hours=1) <= now_naive]
                if done.empty or done.index[-1] < today:
                    continue
                chart = done
            try:
                sigs = evaluate(chart, d, h, tf, p, last_n=1 if tf == "D" else 2)
            except Exception as e:
                log.warning("%s gagal dihitung: %s", t, e)
                continue
            for s in sigs:
                ok = s.name in (allow & index_sigs[t] if t in index_sigs else allow)
                if ok and s.bar_time >= today:
                    found.append((t, s))

        if mode == "daily-preclose":
            state.d["preclose"][day_str] = [sig_key(t, "D", s) for t, s in found]
            header = (f"⏳ <b>SINYAL SEMENTARA — Daily</b>\n{now:%a %d %b %Y, %H:%M} WIB\n"
                      "<i>Candle belum close, bisa berubah sampai penutupan.</i>")
            msgs = notify.build_messages(header, found)
        elif mode == "daily-final":
            new = [(t, s) for t, s in found if not state.seen(sig_key(t, "D", s))]
            pre = set(state.d["preclose"].get(day_str, []))
            final_keys = {sig_key(t, "D", s) for t, s in found}
            batal = sorted(k for k in pre - final_keys)
            for t, s in new:
                state.mark(sig_key(t, "D", s), day_str)
            footer = ""
            if batal:
                rows = [f"• {k.split('|')[0]} {k.split('|')[2]} {k.split('|')[3]}" for k in batal]
                footer = "❌ <b>Sinyal sementara yang BATAL</b>\n" + "\n".join(rows)
            header = f"✅ <b>SINYAL FINAL — Daily</b>\n{now:%a %d %b %Y} (setelah close)"
            msgs = notify.build_messages(header, new, footer)
            if not msgs and footer:
                msgs = [header + "\n\nTidak ada sinyal baru.\n\n" + footer]
        else:  # hourly
            new = [(t, s) for t, s in found if not state.seen(sig_key(t, "1H", s))]
            for t, s in new:
                state.mark(sig_key(t, "1H", s), day_str)
            bars = sorted({s.bar_time for _, s in new})
            when = ", ".join(f"{b:%H:%M}" for b in bars)
            header = f"🕐 <b>SINYAL 1H</b> — candle {when}\n{now:%a %d %b %Y}"
            msgs = notify.build_messages(header, new)

        log.info("%s: %d sinyal, %d pesan", mode, len(found), len(msgs))
        tg.send_all(msgs)
    state.save(today)


def auto_modes(now):
    """Tentukan mode berdasarkan jam WIB (toleran terhadap delay jadwal)."""
    if now.weekday() >= 5:
        return []
    hm = now.hour * 60 + now.minute
    modes = []
    if 10 * 60 <= hm <= 16 * 60 + 59:
        modes.append("hourly")
    if 14 * 60 + 30 <= hm < 15 * 60 + 45:
        modes.append("daily-preclose")
    if 16 * 60 <= hm <= 19 * 60:
        modes.append("daily-final")
    return modes


# ----------------------------------------------------------------------
def cmd_check(cfg, ticker, tf, bars):
    p = params_from(cfg)
    index_sigs = setup_indices(cfg)
    ticker = ticker.upper().replace(".JK", "")
    d = data.download([ticker], cfg.get("daily_period", "3y"), "1d").get(ticker)
    h = data.download([ticker], cfg.get("hourly_period", "1y"), "1h").get(ticker)
    if d is None or h is None:
        print("Data tidak ditemukan untuk", ticker)
        return
    chart = d if tf == "D" else h
    sigs = evaluate(chart, d, h, tf, p, last_n=bars)
    if ticker in index_sigs:
        sigs = [s for s in sigs if s.name in index_sigs[ticker]]
    print(f"\nSinyal {ticker} timeframe {tf}, {bars} bar terakhir:\n")
    for s in sigs:
        mm = (f"  SL {s.info['sl']:,.0f}  TP1 {s.info['tp1']:,.0f}  TP2 {s.info['tp2']:,.0f}"
              if "sl" in s.info else f"  stop Illu {s.info['stop']:,.0f}")
        when = f"{s.bar_time:%Y-%m-%d}" if tf == "D" else f"{s.bar_time:%Y-%m-%d %H:%M}"
        print(f"{when}  {s.name:8s} {s.side:4s}  close {s.close:,.0f}{mm}")
    if not sigs:
        print("(tidak ada sinyal)")


def cmd_backtest(cfg, args, now):
    from . import backtest

    out = ROOT / args.out
    df, summary = backtest.run(
        cfg, args.start, args.end, tuple(args.tf), out,
        index_sigs=setup_indices(cfg), enabled=enabled, params=params_from(cfg),
        liquid=liquid, root=ROOT, now=pd.Timestamp(now.replace(tzinfo=None)))
    if summary.empty:
        print("Tidak ada sinyal pada periode ini")
        return
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print("\n" + summary.round(1).to_string(index=False))
    print(f"\nDetail: {out}")
    if args.telegram:
        start = pd.Timestamp(args.start)
        end = pd.Timestamp(args.end) if args.end else None
        notify.Telegram().send(backtest.telegram_summary(summary, start, end))


def cmd_chat_id():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Set dulu TELEGRAM_BOT_TOKEN")
        return
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
    ids = {(u.get("message") or u.get("channel_post") or {}).get("chat", {}).get("id")
           for u in r.get("result", [])}
    ids.discard(None)
    print("Chat ID ditemukan:", ids or "belum ada — kirim pesan apa saja ke bot dulu, lalu ulangi")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("auto")
    a.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("modes", nargs="+", choices=["hourly", "daily-preclose", "daily-final"])
    r.add_argument("--dry-run", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("ticker")
    c.add_argument("--tf", choices=["D", "1H"], default="D")
    c.add_argument("--bars", type=int, default=120)
    b = sub.add_parser("backtest")
    b.add_argument("--start", default="2026-09-01")
    b.add_argument("--end", default=None)
    b.add_argument("--tf", nargs="+", choices=["D", "1H"], default=["D", "1H"])
    b.add_argument("--out", default="backtest_hasil.xlsx")
    b.add_argument("--telegram", action="store_true", help="kirim ringkasan ke Telegram")
    sub.add_parser("test-telegram")
    sub.add_parser("chat-id")
    args = ap.parse_args()

    cfg = load_config()
    now = datetime.now(WIB)
    if args.cmd == "check":
        return cmd_check(cfg, args.ticker, args.tf, args.bars)
    if args.cmd == "backtest":
        return cmd_backtest(cfg, args, now)
    if args.cmd == "chat-id":
        return cmd_chat_id()
    if args.cmd == "test-telegram":
        ok = notify.Telegram().send("✅ MacroFlow Alert terhubung. Notifikasi sinyal akan dikirim ke sini.")
        print("Terkirim" if ok else "Gagal")
        return
    modes = auto_modes(now) if args.cmd == "auto" else args.modes
    if not modes:
        log.info("Di luar jam jadwal (%s WIB) — tidak ada yang dijalankan", f"{now:%a %H:%M}")
        return
    tg = notify.Telegram(dry_run=args.dry_run)
    run_modes(modes, cfg, now, tg, State(ROOT / "state.json"))


if __name__ == "__main__":
    main()
