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
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yaml

from . import data, index_alert, market, notify
from .signals import Params, evaluate

ROOT = Path(__file__).resolve().parent.parent
WIB = ZoneInfo("Asia/Jakarta")
log = logging.getLogger("macroflow")


# ----------------------------------------------------------------------
def load_config():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text()) or {}
    return cfg


def setup_indices(cfg):
    """Daftarkan indeks yang ikut scan saham (config `indices`, default kosong) + simbol indeks
    dari `index_alert`. Return {kode: set sinyal} untuk indeks yang ikut scan saham."""
    index_alert.register(cfg)
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
            cur = getattr(p, k)
            if isinstance(cur, bool):  # bool("false") == True, jadi ditangani khusus
                v = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "ya")
            setattr(p, k, type(cur)(v))
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
    idx = [str(c).upper() for c in (cfg.get("indices") or {})]  # indeks ikut scan (default: tidak ada)
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


ALL_SIGNALS = ["ROYAL", "COMBO", "GOLDEN", "STANDAR", "METER"]


def enabled(cfg, tf):
    """Set (nama, arah) sinyal yang aktif untuk timeframe `tf`.

    config `signals.<tf>` boleh berupa list (berlaku BUY & SELL) atau
    dict {BUY: [...], SELL: [...]}.
    """
    spec = (cfg.get("signals") or {}).get(tf, ALL_SIGNALS)
    if isinstance(spec, dict):
        sides = {side: spec.get(side) or [] for side in ("BUY", "SELL")}
    else:
        sides = {"BUY": spec or [], "SELL": spec or []}
    return {(str(n).upper(), side) for side, names in sides.items() for n in names}


def allowed(sig, ticker, allow, index_sigs):
    """Apakah sinyal boleh dikirim (sesuai config timeframe & daftar sinyal indeks)."""
    if (sig.name, sig.side) not in allow:
        return False
    return ticker not in index_sigs or sig.name in index_sigs[ticker]


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

    # --- status IHSG + peringatan keras (Daily & Weekly) ---------------
    status = market_status(cfg, now_naive, p)
    status_txt = ("\n\n" + status.text()) if status else ""
    if status and status.warning and not done_days.get(f"warn|{status.code}|{day_str}"):
        done_days[f"warn|{status.code}|{day_str}"] = True
        tg.send_all([status.warning_text() + f"\n\n<i>{now:%d/%m %H:%M} WIB</i>"])

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
                if allowed(s, t, allow, index_sigs) and s.bar_time >= today:
                    found.append((t, s))

        if mode == "daily-preclose":
            state.d["preclose"][day_str] = [sig_key(t, "D", s) for t, s in found]
            header = (f"⏳ <b>SINYAL SEMENTARA — Daily</b>\n{now:%a %d %b %Y, %H:%M} WIB\n"
                      "<i>Candle belum close, bisa berubah sampai penutupan.</i>" + status_txt)
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
            header = f"✅ <b>SINYAL FINAL — Daily</b>\n{now:%a %d %b %Y} (setelah close)" + status_txt
            msgs = notify.build_messages(header, new, footer)
            if not msgs and footer:
                msgs = [header + "\n\nTidak ada sinyal baru.\n\n" + footer]
        else:  # hourly
            new = [(t, s) for t, s in found if not state.seen(sig_key(t, "1H", s))]
            for t, s in new:
                state.mark(sig_key(t, "1H", s), day_str)
            bars = sorted({s.bar_time for _, s in new})
            when = ", ".join(f"{b:%H:%M}" for b in bars)
            header = f"🕐 <b>SINYAL 1H</b> — candle {when}\n{now:%a %d %b %Y}" + status_txt
            msgs = notify.build_messages(header, new, show_time=len(bars) > 1)

        log.info("%s: %d sinyal, %d pesan", mode, len(found), len(msgs))
        tg.send_all(msgs)
    state.save(today)


def market_kwargs(mw):
    """Setelan peringatan dari config `market_warning` (min_strong_sell = nama lama min_events)."""
    return dict(
        window_days=int(mw.get("window_days", 5)),
        min_events=int(mw.get("min_events", mw.get("min_strong_sell", 2))),
        signals=tuple(str(x).upper() for x in mw.get("signals", market.DEFAULT_SIGNALS)),
        timeframes=tuple(str(x).upper() for x in mw.get("timeframes", ["D", "W"])
                         if str(x).upper() in ("D", "W")))  # 1H tidak dipakai untuk indeks


def market_status(cfg, now_naive, p):
    """Status indeks acuan (default IHSG, Daily & Weekly, Meter SMA) untuk header notifikasi saham."""
    mw = cfg.get("market_warning") or {}
    code = str(mw.get("index", "IHSG")).upper()
    try:
        d = data.download([code], cfg.get("index_period", "5y"), "1d").get(code)
        if d is None or len(d) < 60:
            return None
        sp = {x["code"]: x for x in index_alert.specs(cfg)}.get(code, {"name": code, "market": "IDX"})
        fu = index_alert.final_upto(d, now_naive, sp["market"])
        return market.ihsg_status(d, now_naive, index_alert.index_params(cfg, p), code=code,
                                  name=sp["name"], final_upto=fu, **market_kwargs(mw))
    except Exception as e:  # status hanya pelengkap, jangan gagalkan notifikasi
        log.warning("Status %s gagal dihitung: %s", code, e)
        return None


STOCK_MODES = ("hourly", "daily-preclose", "daily-final")
INDEX_MODES = {"index-idx": "IDX", "index-us": "US"}


def auto_modes(now):
    """Tentukan mode berdasarkan jam WIB (toleran terhadap delay jadwal)."""
    hm = now.hour * 60 + now.minute
    modes = []
    if now.weekday() < 5:  # Senin-Jumat: bursa Indonesia
        if 10 * 60 <= hm <= 16 * 60 + 59:
            modes.append("hourly")
        if 14 * 60 + 30 <= hm < 15 * 60 + 45:
            modes.append("daily-preclose")
        if 16 * 60 <= hm <= 19 * 60:
            modes += ["daily-final", "index-idx"]
    # Selasa-Sabtu pagi WIB: bursa AS hari sebelumnya sudah tutup
    if 1 <= now.weekday() <= 5 and 5 * 60 <= hm <= 7 * 60 + 59:
        modes.append("index-us")
    return modes


# ----------------------------------------------------------------------
def cmd_check(cfg, ticker, tf, bars):
    p = params_from(cfg)
    index_sigs = setup_indices(cfg)
    ticker = ticker.upper().replace(".JK", "")
    idx_codes = {sp["code"] for sp in index_alert.specs(cfg)}
    is_index = ticker in idx_codes
    period = cfg.get("index_period", "5y") if is_index else cfg.get("daily_period", "3y")
    d = data.download([ticker], period, "1d").get(ticker)
    h = None
    if not is_index:
        h = data.download([ticker], cfg.get("hourly_period", "1y"), "1h").get(ticker)
    if d is None or (h is None and not is_index):
        print("Data tidak ditemukan untuk", ticker)
        return
    if is_index:  # alert indeks: Meter SMA, hanya D & W, tanpa ROYAL/GOLDEN
        if tf == "1H":
            print("Indeks hanya memakai timeframe D dan W")
            return
        p = index_alert.index_params(cfg, p)
        names, _ = index_alert.settings(cfg)
        d = index_alert._novol(d)
    chart = {"D": d, "1H": h, "W": market.weekly_bars(d)}[tf]
    sigs = evaluate(chart, d, h, tf, p, last_n=bars)
    if is_index:
        sigs = [s for s in sigs if s.name in names]
    elif ticker in index_sigs:
        sigs = [s for s in sigs if s.name in index_sigs[ticker]]
    note = f" (Meter {p.meter_ma}50)" if is_index else ""
    print(f"\nSinyal {ticker} timeframe {tf}, {bars} bar terakhir{note}:\n")
    for s in sigs:
        mm = (f"  SL {s.info['sl']:,.0f}  TP1 {s.info['tp1']:,.0f}  TP2 {s.info['tp2']:,.0f}"
              if "sl" in s.info and not is_index else f"  stop Illu {s.info['stop']:,.2f}")
        when = f"{s.bar_time:%Y-%m-%d %H:%M}" if tf == "1H" else f"{s.bar_time:%Y-%m-%d}"
        print(f"{when}  {s.name:8s} {s.side:4s}  close {s.close:,.2f}{mm}")
    if not sigs:
        print("(tidak ada sinyal)")


def cmd_backtest(cfg, args, now):
    from . import backtest

    try:
        start = pd.Timestamp(args.start.strip())
        end = pd.Timestamp(args.end.strip()) if args.end and args.end.strip() else None
    except ValueError:
        print("GAGAL: format tanggal salah. Pakai YYYY-MM-DD, contoh 2026-04-01.")
        sys.exit(1)
    if end is not None and end < start:
        print("GAGAL: tanggal akhir lebih awal dari tanggal mulai.")
        sys.exit(1)
    args.start = f"{start:%Y-%m-%d}"
    args.end = f"{end:%Y-%m-%d}" if end is not None else None
    print(f"Backtest {args.start} s/d {args.end or 'hari ini'}, timeframe {' & '.join(args.tf)}")

    out = ROOT / args.out
    df, summary = backtest.run(
        cfg, args.start, args.end, tuple(args.tf), out,
        index_sigs=setup_indices(cfg), enabled=enabled, allowed=allowed, params=params_from(cfg),
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


def cmd_ihsg_study(cfg, args, now):
    from . import study

    setup_indices(cfg)
    out = ROOT / args.out
    summary, reg = study.run(cfg, params_from(cfg), out, now=pd.Timestamp(now.replace(tzinfo=None)))
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(summary.round(1).to_string(index=False))
        print()
        print(reg.round(1).to_string(index=False))
    print(f"\nDetail: {out}")
    if args.telegram:
        notify.Telegram().send_all(notify.split_messages(
            "🔬 <b>STUDI POLA IHSG</b>", [study.telegram_text(summary, reg).split("\n", 1)[1]]))


def cmd_test_telegram():
    """Cek token & chat ID langkah demi langkah, tampilkan penyebab kalau gagal."""
    import re

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    print(f"[1] Secret TELEGRAM_BOT_TOKEN: {'ADA' if token else 'KOSONG'}"
          f" | TELEGRAM_CHAT_ID: {'ADA' if chat else 'KOSONG'}")
    if not token or not chat:
        print("GAGAL: secret belum diisi atau namanya tidak persis sama "
              "(harus TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID).")
        return False
    if not re.fullmatch(r"\d+:[\w-]{30,}", token):
        print("GAGAL: format token salah. Harus seperti 1234567890:AAH... "
              "(tanpa spasi, tanpa tanda kutip, tanpa kata 'bot' di depan).")
        return False
    if not re.fullmatch(r"-?\d+", chat):
        print("GAGAL: chat ID harus berupa angka saja, contoh 123456789 (grup: -100...).")
        return False

    base = f"https://api.telegram.org/bot{token}"
    me = requests.get(f"{base}/getMe", timeout=30).json()
    if not me.get("ok"):
        print(f"[2] GAGAL cek token: {me.get('description')}")
        print("    -> Token salah / sudah di-revoke. Isi ulang dengan token TERBARU dari @BotFather.")
        return False
    print(f"[2] Token valid. Nama bot: @{me['result']['username']}")

    r = requests.post(f"{base}/sendMessage", timeout=30, json={
        "chat_id": chat,
        "text": "✅ MacroFlow Alert terhubung. Notifikasi sinyal akan dikirim ke sini."}).json()
    if r.get("ok"):
        print(f"[3] Terkirim ke chat {chat}. Cek chat dengan @{me['result']['username']} di Telegram.")
        return True
    desc = r.get("description", "")
    print(f"[3] GAGAL kirim: {desc}")
    if "chat not found" in desc:
        print(f"    -> Chat ID salah, atau kamu belum menekan START di @{me['result']['username']}.")
    elif "blocked" in desc:
        print(f"    -> Kamu memblokir @{me['result']['username']}. Unblock lalu tekan START.")
    elif "initiate" in desc or "Forbidden" in desc:
        print(f"    -> Buka @{me['result']['username']} di Telegram dan tekan START dulu.")
    return False


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
    r.add_argument("modes", nargs="+", choices=list(STOCK_MODES) + list(INDEX_MODES))
    r.add_argument("--dry-run", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("ticker")
    c.add_argument("--tf", choices=["D", "1H", "W"], default="D")
    c.add_argument("--bars", type=int, default=120)
    b = sub.add_parser("backtest")
    b.add_argument("--start", default="2026-09-01")
    b.add_argument("--end", default=None)
    b.add_argument("--tf", nargs="+", choices=["D", "1H"], default=["D", "1H"])
    b.add_argument("--out", default="backtest_hasil.xlsx")
    b.add_argument("--telegram", action="store_true", help="kirim ringkasan ke Telegram")
    st = sub.add_parser("ihsg-study", help="uji pola sinyal IHSG (rezim EMA200, kumpulan sinyal)")
    st.add_argument("--out", default="ihsg_study.xlsx")
    st.add_argument("--telegram", action="store_true", help="kirim ringkasan ke Telegram")
    sub.add_parser("test-telegram")
    sub.add_parser("chat-id")
    args = ap.parse_args()

    cfg = load_config()
    now = datetime.now(WIB)
    if args.cmd == "check":
        return cmd_check(cfg, args.ticker, args.tf, args.bars)
    if args.cmd == "backtest":
        return cmd_backtest(cfg, args, now)
    if args.cmd == "ihsg-study":
        return cmd_ihsg_study(cfg, args, now)
    if args.cmd == "chat-id":
        return cmd_chat_id()
    if args.cmd == "test-telegram":
        sys.exit(0 if cmd_test_telegram() else 1)
    modes = auto_modes(now) if args.cmd == "auto" else args.modes
    if not modes:
        log.info("Di luar jam jadwal (%s WIB) — tidak ada yang dijalankan", f"{now:%a %H:%M}")
        return
    tg = notify.Telegram(dry_run=args.dry_run)
    state = State(ROOT / "state.json")
    stock = [m for m in modes if m in STOCK_MODES]
    if stock:
        run_modes(stock, cfg, now, tg, state)
    for m in modes:
        if m in INDEX_MODES:
            index_alert.run(cfg, now, tg, state, INDEX_MODES[m], params_from(cfg),
                            market_kwargs(cfg.get("market_warning") or {}))


if __name__ == "__main__":
    main()
