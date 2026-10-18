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
SHORT = {"ROYAL": "👑ROYAL", "COMBO": "🔷COMBO", "GOLDEN": "🌟GOLDEN",
         "STANDAR": "📍STANDAR", "METER": "📶STRONG"}
LEGEND = ("<i>👑ROYAL=H4+D+W+Vol · 🔷COMBO=Illu+EMA50+MACD+RSI · 📍STANDAR=Illu Trx · "
          "📶STRONG=Meter STRONG · 🌟GOLDEN=MA200+Stoch</i>")


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


def merge_signals(items):
    """Gabung sinyal ganda: satu entri per (saham, arah, candle).

    Return list (ticker, side, bar_time, [Signal...]) — sinyal diurutkan sesuai ORDER.
    """
    groups = {}
    for t, s in items:
        groups.setdefault((t, s.side, s.bar_time), []).append(s)
    out = []
    for (t, side, bt), sigs in groups.items():
        sigs = sorted({s.name: s for s in sigs}.values(), key=lambda s: ORDER.index(s.name))
        out.append((t, side, bt, sigs))
    return out


def _merged_line(ticker, sigs, show_time=False):
    names = " + ".join(SHORT[s.name] for s in sigs)
    when = f" · {sigs[0].bar_time:%H:%M}" if show_time else ""
    # baris harga/SL memakai sinyal pertama (semua sinyal di candle yang sama)
    return fmt_signal_line(ticker, sigs[0]).replace("\n", f" · <b>{names}</b>{when}\n", 1)


def build_messages(header, items, footer="", show_time=False):
    """items: list (ticker, Signal). Satu baris per saham per candle, dikelompokkan per arah.

    Urutan: saham dengan sinyal terbanyak dulu, lalu sinyal terkuat (ROYAL > COMBO > ...),
    lalu rasio volume tertinggi.
    """
    merged = merge_signals(items)
    blocks = []
    # indeks (mis. IHSG) selalu paling atas
    for idx in TV_SYMBOLS:
        rows = [m for m in merged if m[0] == idx]
        if rows:
            lines = [f"📊 <b>Sinyal {esc(idx)}</b>"]
            for t, side, bt, sigs in sorted(rows, key=lambda m: (m[1] != "BUY", m[2])):
                emoji = "🟢" if side == "BUY" else "🔴"
                lines.append(f"{emoji} {side} · " + _merged_line(t, sigs, show_time))
            blocks.append("\n".join(lines))
    merged = [m for m in merged if m[0] not in TV_SYMBOLS]

    def key(m):
        sigs = m[3]
        return (-len(sigs), ORDER.index(sigs[0].name), -(sigs[0].info.get("vol_ratio") or 0), m[0])

    for side, emoji in (("BUY", "🟢"), ("SELL", "🔴")):
        rows = sorted((m for m in merged if m[1] == side), key=key)
        if not rows:
            continue
        lines = [f"{emoji} <b>{side}</b> — {len({m[0] for m in rows})} saham"]
        lines += [_merged_line(t, sigs, show_time) for t, _, _, sigs in rows]
        blocks.append("\n".join(lines))
    if not blocks:
        return []
    blocks.append(LEGEND)
    return split_messages(header, blocks, footer)


def split_messages(header, blocks, footer="", limit=3900):
    # pesan lanjutan cukup memakai baris pertama header (tanpa blok status)
    cont = header.split("\n")[0] + " (lanjutan)"
    msgs, cur = [], header
    for b in blocks:
        for piece in _split_block(b, limit - len(cont) - 10):
            if len(cur) + len(piece) + 2 > limit:
                msgs.append(cur)
                cur = cont
            cur += "\n\n" + piece
    if footer:
        if len(cur) + len(footer) + 2 > limit:
            msgs.append(cur)
            cur = cont
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
