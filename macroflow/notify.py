"""Format pesan & kirim ke Telegram."""
import html
import logging
import os

import numpy as np
import requests

log = logging.getLogger(__name__)

ORDER = ["ROYAL", "COMBO", "GOLDEN", "STANDAR", "METER"]
TITLE = {
    "ROYAL": "👑 ROYAL (H4+D+W+Vol)",
    "COMBO": "🔷 COMBO (Illu+EMA50+MACD+RSI)",
    "GOLDEN": "🌟 GOLDEN SETUP (MA200+Stoch)",
    "STANDAR": "📍 STANDAR (Illu Trx)",
    "METER": "📶 METER STRONG",
}


def fmt_price(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"{x:,.0f}".replace(",", ".")


# kode khusus (mis. indeks) -> simbol TradingView; diisi dari config
TV_SYMBOLS = {}


def fmt_signal_line(ticker, s, prefix=""):
    url = f"https://www.tradingview.com/chart/?symbol={TV_SYMBOLS.get(ticker, 'IDX:' + ticker)}"
    chg = s.info.get("chg")
    chg_txt = "" if chg is None or np.isnan(chg) else f" ({chg:+.1f}%)"
    line = f'{prefix}<a href="{url}"><b>{ticker}</b></a> {fmt_price(s.close)}{chg_txt}'
    extra = []
    rsi = s.info.get("rsi")
    if rsi is not None and not np.isnan(rsi):
        extra.append(f"RSI {rsi:.0f}")
    vr = s.info.get("vol_ratio")
    if vr is not None and not np.isnan(vr):
        extra.append(f"Vol {vr:.1f}x")
    if s.side == "BUY" and "sl" in s.info:
        extra.append(f"SL {fmt_price(s.info['sl'])} · TP1 {fmt_price(s.info['tp1'])} · "
                     f"TP2 {fmt_price(s.info['tp2'])}")
    else:
        extra.append(f"Stop Illu {fmt_price(s.info.get('stop'))}")
    if extra:
        line += "\n   " + " | ".join(extra)
    return line


def build_messages(header, items, footer=""):
    """items: list (ticker, Signal). Kelompokkan per jenis & sisi."""
    blocks = []
    # indeks (mis. IHSG) selalu paling atas
    for idx in TV_SYMBOLS:
        rows = [s for t, s in items if t == idx]
        if rows:
            lines = [f"📊 <b>{esc(idx)}</b>"]
            for s in sorted(rows, key=lambda s: (s.side != "BUY", ORDER.index(s.name))):
                emoji = "🟢" if s.side == "BUY" else "🔴"
                lines.append(fmt_signal_line(idx, s, f"{emoji} {s.name} {s.side} · "))
            blocks.append("\n".join(lines))
    items = [(t, s) for t, s in items if t not in TV_SYMBOLS]
    for side, emoji in (("BUY", "🟢"), ("SELL", "🔴")):
        for name in ORDER:
            rows = [(t, s) for t, s in items if s.name == name and s.side == side]
            if not rows:
                continue
            rows.sort(key=lambda r: -(r[1].info.get("vol_ratio") or 0))
            lines = [f"{emoji} <b>{TITLE[name]} — {side}</b> ({len(rows)})"]
            lines += [fmt_signal_line(t, s) for t, s in rows]
            blocks.append("\n".join(lines))
    if not blocks:
        return []
    return split_messages(header, blocks, footer)


def split_messages(header, blocks, footer="", limit=3900):
    msgs, cur = [], header
    for b in blocks:
        for piece in _split_block(b, limit - len(header) - 10):
            if len(cur) + len(piece) + 2 > limit:
                msgs.append(cur)
                cur = header + " (lanjutan)"
            cur += "\n\n" + piece
    if footer:
        if len(cur) + len(footer) + 2 > limit:
            msgs.append(cur)
            cur = header + " (lanjutan)"
        cur += "\n\n" + footer
    msgs.append(cur)
    return msgs


def _split_block(block, limit):
    if len(block) <= limit:
        return [block]
    lines = block.split("\n")
    out, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > limit:
            out.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + ln
    if cur:
        out.append(cur)
    return out


def esc(text):
    return html.escape(str(text))


class Telegram:
    def __init__(self, token=None, chat_id=None, dry_run=False):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.dry_run = dry_run or not (self.token and self.chat_id)
        if self.dry_run and not dry_run:
            log.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum di-set -> hanya print")

    def send(self, text):
        if self.dry_run:
            print("=" * 60 + "\n" + text + "\n")
            return True
        r = requests.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=30,
        )
        if not r.ok:
            log.error("Telegram error %s: %s", r.status_code, r.text[:300])
        return r.ok

    def send_all(self, msgs):
        for m in msgs:
            self.send(m)
