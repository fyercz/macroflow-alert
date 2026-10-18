# CLAUDE.md — konteks proyek untuk Claude

File ini ditulis Claude untuk dirinya sendiri di sesi berikutnya. Baca sampai habis sebelum mengubah apa pun.

## Ringkasan

App reminder sinyal BUY/SELL saham IDX → Telegram, hasil port indikator TradingView
**MacroFlow V65** (Pine Script asli seharusnya di `docs/macroflow_v65.pine` — **belum ada di repo**, minta pengguna upload bila perlu). Berjalan gratis di GitHub Actions
(repo `fyercz/macroflow-alert`, public). Data dari Yahoo Finance via `yfinance` (delay ±10 menit, tidak perlu realtime).

Pengguna: **agit** (akun GitHub `fyercz`), zona waktu WIB (Asia/Jakarta).
Bicara dengan pengguna dalam **Bahasa Indonesia**, santai & jelas; pengguna bukan programmer —
beri langkah klik-per-klik di GitHub/Telegram, jangan suruh pakai terminal kecuali diminta.

## Keputusan yang sudah disepakati (jangan diubah tanpa ditanya)

- **Universe**: semua saham IDX (daftar dari screener TradingView, cache `tickers.txt` 7 hari),
  difilter likuiditas: rata-rata nilai transaksi 20 hari ≥ Rp 2 M, harga ≥ 50.
- **Tanpa scanner/tabel/fibo** dari Pine — hanya sinyal.
- **Timeframe**: Daily + 1H. Jadwal (WIB, Sen–Jum):
  1H tiap 10:15, 11:15, 12:15, 14:15, 15:15, 16:15 · Daily SEMENTARA 14:45 · Daily FINAL 16:15
  (+ daftar sinyal sementara yang batal). Cron di `.github/workflows/alert.yml` dalam UTC (WIB−7);
  `main.auto_modes()` menentukan mode dari jam WIB, toleran delay cron GitHub.
- **Sinyal**: ROYAL, COMBO, GOLDEN, STANDAR (Illu Trx), METER STRONG — semua aktif di D & 1H.
- **IHSG** (`^JKSE`, TradingView `IDX:COMPOSITE`) selalu dicek tanpa filter, tampil paling atas,
  **ROYAL dimatikan untuk IHSG** (volume indeks Yahoo tidak akurat). Diatur di `indices:` config.
- **Money management**: jumlah lot / modal / risiko **DIHAPUS** atas permintaan pengguna,
  tetapi **SL, TP1, TP2 TETAP ADA** untuk sinyal BUY: SL = close − 2×ATR14, TP1 = 2R, TP2 = 3R,
  dibulatkan ke bawah sesuai fraksi harga BEI. Sinyal SELL menampilkan trailing stop Illu Trx.
- Notifikasi dikelompokkan per jenis sinyal, nama saham = link chart TradingView.

## Struktur kode

| File | Isi |
|---|---|
| `macroflow/indicators.py` | EMA/RMA (seed SMA seperti Pine), RSI, ATR, DMI/ADX, MACD, Stoch, crossover, `illu_trx` (UT Bot) — sudah dicocokkan dengan TA-Lib (selisih < 1e-12) |
| `macroflow/signals.py` | Pembentukan H4 (09–13 & 13–17 WIB, pola TradingView IDX), D parsial, W; `meter()`; `evaluate()` → list `Signal`; `sl_tp()` |
| `macroflow/data.py` | Daftar saham (TradingView scanner), download Yahoo per 80 ticker, `YAHOO_SYMBOLS` untuk indeks |
| `macroflow/notify.py` | Format HTML Telegram, split 3900 char, `TV_SYMBOLS` untuk indeks |
| `macroflow/main.py` | CLI: `auto`, `run <mode>`, `check TICKER --tf D/1H`, `backtest --start [--end] [--tf D 1H]` (tanggal divalidasi, exit 1 kalau salah), `test-telegram` (diagnostik 3 langkah, exit 1 kalau gagal), `chat-id` |
| `macroflow/backtest.py` | Putar ulang sinyal sejak tanggal X, simulasi SL/TP1/TP2, return 5/10 bar, Excel 4 sheet, ringkasan Telegram |
| `config.yaml` | Semua setelan yang boleh diubah pengguna |
| `tests/test_smoke.py` | Tes offline dengan data sintetis — **jalankan setiap habis mengubah kode** |

State antar-run (`state.json`: dedup sinyal, preclose, done-flag) & `tickers.txt` disimpan lewat `actions/cache`.

### Detail logika yang mudah salah
- ROYAL = meter H4, D, W **semua** BUY (atau SELL) + volume > SMA20 + candle hijau (merah); trigger saat kondisi baru muncul.
  Skor HTF dihitung "as-of" penutupan candle chart (seperti realtime, tidak repaint) dan hanya dihitung
  bila syarat volume terpenuhi (optimasi; hasil identik).
- Sinyal 1H hanya dari candle yang **sudah close** (`index + 1h <= now`).
- Daily preclose & final masing-masing sekali per hari (`state["done"]`).
- Backtest: entry = close candle sinyal; SL & TP di candle sama → dihitung SL.

## Lingkungan & batasan Claude (penting)

- Sandbox Claude **tidak bisa** mengakses Yahoo, TradingView, Telegram (egress diblokir). Uji dengan
  `tests/test_smoke.py` (data sintetis, mock download). Data asli hanya jalan di GitHub Actions / komputer pengguna.
- `raw.githubusercontent.com` bisa diakses → cek isi repo public dengan membandingkan file lokal vs raw.
- Push ke GitHub hanya bisa kalau repo dilampirkan sebagai sumber sesi **atau** pengguna memberi PAT di chat.
  Pakai PAT hanya lewat `git -c http.extraHeader=...` per perintah — jangan simpan di `.git/config`/file,
  dan sarankan pengguna mencabut token setelah sesi. Kalau tidak ada akses, kirim file pengganti dan pandu
  pengguna edit lewat `github.com/fyercz/macroflow-alert/edit/main/<path>`.
- **Jangan pernah** menulis token Telegram ke file mana pun. Token & chat ID hanya di
  GitHub Secrets `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`. Bot: @menanglanekalahgamebot.
- Versi action Node 24: checkout@v5, setup-python@v6, cache@v5, upload-artifact@v6.

## Status terakhir (19 Okt 2026)

- `tests/test_smoke.py` & `CLAUDE.md` ditambahkan ke repo (9 tes, ±25 detik, jalan tanpa pytest:
  `python tests/test_smoke.py`). Tes tidak dijalankan di GitHub Actions (hanya lokal).
- Repo terpasang, secret terisi, `test-telegram` **berhasil**. Jadwal otomatis mulai Senin 19 Okt 2026.
- Backtest pertama (data asli, 14–16 Okt 2026, 314 saham) sudah dianalisis — **tidak representatif**:
  periode itu crash IHSG (16 Okt −11,57% ke 5.185, trading halt, isu MSCI). Hasil: 477 sinyal D + 1.818 sinyal 1H
  dalam 3 hari (terlalu ramai, sering 2–4 sinyal di candle yg sama); hampir semua BUY kena SL (win rate 0–11%),
  SELL 1H benar 54–67%. Daily belum punya data 5 bar.
- Menu Run workflow sekarang punya input backtest `start` (default 2026-04-01), `end` (kosong = hari ini),
  `tf` (D dan 1H / D / 1H); input dilewatkan via env (aman dari injeksi). Timeout job backtest 180 menit
  (lainnya 50). Estimasi: ±6 detik/saham untuk 6 bulan D+1H (1H dominan) → ±30–60 menit untuk ~314 saham.
- Menunggu: pengguna backtest ulang periode normal (usul Apr–Sep 2026). Usulan belum dikerjakan:
  filter pasar (tahan BUY saat meter IHSG SELL), gabung sinyal ganda per saham per candle.

## Ide lanjutan yang pernah ditawarkan (belum dikerjakan)

- Input tanggal backtest `5d` = 5 hari bursa terakhir otomatis.
- Kurangi kebisingan ROYAL/METER di 1H (mis. cooldown, atau hapus dari `signals.1H`) setelah lihat hasil backtest.
- Ganti sumber data (EODHD $29.99/bln intraday — cek dulu dukungan `.JK` 1h; Invezgo lokal) bila Yahoo bermasalah;
  cukup ganti `data.download()`.
