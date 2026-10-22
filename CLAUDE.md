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

- **Candle Heikin Ashi** (pengguna, 23 Okt 2026: "indikatornya didesain menggunakan heikin ashi"): `Params.heikin_ashi=True`.
  `signals.candles()` mengubah OHLC asli → HA; `meter()` & `evaluate()` SELALU menerima candle asli lalu konversi sendiri
  (jangan kirim HA ke keduanya — dobel konversi). HTF (H4/D/W) disusun dari candle asli lalu HA (seperti request.security
  di chart HA). Harga tampil, chg, SL/TP (ATR asli), dan simulasi backtest = **harga asli**; "Stop Illu" = skala HA.
  Status/rezim (market.py) & rezim study memakai HA close. Hasil backtest/ihsg-study sebelum 23 Okt = candle biasa (usang).
- **Semua teks Telegram dalam bahasa INGGRIS** (pengguna 23 Okt 2026: istilah Indonesia membingungkan) — termasuk
  status, peringatan ("STRONG WARNING"), alert indeks, backtest & study summary, test-telegram. Nama sinyal tampil
  "STANDARD" (internal tetap `STANDAR`), tren UP/DOWN/MIXED & rezim TRANSITION via `market.TREND_EN/REGIME_EN`
  (nilai internal NAIK/TURUN/CAMPURAN/TRANSISI). Angka format Inggris (1,273 · 6,712.45), tanggal "Fri Oct 16".
  Tes `test_alerts_in_english` menjaga ini. Chat dengan pengguna tetap Bahasa Indonesia; Excel/console boleh Indonesia.
- **SMC** (pengguna 23 Okt 2026: "sinyal SMC baru", komponen BOS/CHoCH, Order Block, FVG; saham & indeks):
  `macroflow/smc.py` `detect(o,h,l,c,swing)` di candle HA, non-repaint (pivot dikonfirmasi `smc_swing`=5 bar kemudian;
  tes `test_smc_no_repaint`). Nama sinyal CHOCH/BOS/OB/FVG di `evaluate()`; OB = retest pertama zona (candle low
  terendah/high tertinggi antara pivot & candle penembus), batal bila close tembus sisi seberang, maks 5 zona/sisi;
  FVG = celah 3 candle + body tengah > 2× rata-rata kumulatif |body%|. `info["zone"]` (skala HA) tampil di pesan.
  Aktif: D semua 4, 1H hanya CHOCH & OB (BOS/FVG 1H terlalu sering), indeks D&W semua 4. **Belum di-backtest.**
  Peringatan keras IHSG tetap hanya COMBO/STANDAR/METER.
- **Universe**: semua saham IDX (daftar dari screener TradingView, cache `tickers.txt` 7 hari),
  difilter likuiditas: rata-rata nilai transaksi 20 hari ≥ Rp 2 M, harga ≥ 50.
- **Tanpa scanner/tabel/fibo** dari Pine — hanya sinyal.
- **Timeframe**: Daily + 1H. Jadwal (WIB, Sen–Jum):
  1H tiap 10:15, 11:15, 12:15, 14:15, 15:15, 16:15 · Daily SEMENTARA 14:45 · Daily FINAL 16:15
  (+ daftar sinyal sementara yang batal). Cron di `.github/workflows/alert.yml` dalam UTC (WIB−7);
  `main.auto_modes()` menentukan mode dari jam WIB, toleran delay cron GitHub.
- **Sinyal** (diputuskan 19 Okt 2026 dari backtest Apr–Sep): GOLDEN **mati**. D BUY & SELL: ROYAL, COMBO,
  STANDAR, METER. 1H BUY: **hanya COMBO + METER**; 1H SELL: ROYAL, COMBO, STANDAR, METER.
  Config `signals.<tf>` = dict `{BUY: [...], SELL: [...]}` atau list (kedua arah); `main.enabled()` → set (nama, arah),
  `main.allowed()` juga menerapkan daftar sinyal indeks.
- **Alert indeks** (22 Okt 2026, `macroflow/index_alert.py`, config `index_alert`): IHSG (`^JKSE`, TV `IDX:COMPOSITE`)
  & S&P 500 (`^GSPC`, TV `SP:SPX`), **hanya Daily & Weekly**, sinyal COMBO/STANDAR/METER (tanpa ROYAL/GOLDEN,
  volume di-nol-kan), **Meter STRONG pakai SMA50** (`Params.meter_ma="SMA"`; saham tetap EMA50 — keputusan pengguna).
  Mode `index-idx` (auto 16:00–19:00 WIB Sen–Jum, ikut cron 16:15) & `index-us` (auto 05:00–07:59 WIB Sel–Sab,
  cron `15 22 * * 1-5` UTC). Hanya candle final (`final_upto`: IDX ≥16:00; US bar < hari ini WIB setelah 05:00).
  Weekly = `market.weekly_bars` (index = hari bursa terakhir minggu) + `closed_weeks`; W memakai last_n=1, D last_n=2.
  Dedup key `INDEX|kode|tf|nama|arah|tgl`. Pesan hanya bila ada sinyal baru / peringatan baru.
- **1H tidak dipakai untuk IHSG di mana pun** (keputusan pengguna 22 Okt): IHSG keluar dari scan saham
  (`indices: {}`), status/peringatan/ihsg-study memakai Daily & Weekly.
- **Status IHSG** di header setiap notifikasi saham & di alert indeks (`macroflow/market.py`, download harian sendiri,
  Meter SMA): NAIK/TURUN/CAMPURAN (Illu Trx D + Meter D), harga, EMA50, **rezim EMA200** (UPTREND = close>EMA200 & EMA200 naik 20 hari; DOWNTREND = kebalikan; selain itu
  TRANSISI), Illu Trx D sejak kapan, Meter D & W, jumlah sinyal SELL/BUY dalam 5 hari.
  **Peringatan keras** (permintaan pengguna, diperluas 19 Okt dari pola chart): IHSG punya sinyal SELL
  (COMBO/STANDAR/METER, D **dan** W) di ≥2 **candle berbeda** dalam 5 hari bursa → pesan 🚨 1×/hari per indeks
  (`state["done"]["warn|<kode>|<tgl>"]`) + banner di setiap header. BUY ≥2 candle saat UPTREND → baris "✅ Konfirmasi tren naik".
  Config `market_warning`: index, window_days, min_events (alias lama min_strong_sell), signals, timeframes.
- Pengamatan pengguna dari chart IHSG (2 screenshot TradingView): sinyal searah EMA200 berhasil, melawan EMA200 gagal;
  kumpulan 2–3 sinyal searah dalam beberapa hari mendahului pergerakan besar. **Belum terbukti dengan data** —
  perintah `ihsg-study` dibuat untuk mengujinya (menunggu pengguna menjalankan & upload Excel).
- **Money management**: jumlah lot / modal / risiko **DIHAPUS** atas permintaan pengguna,
  tetapi **SL, TP1, TP2 TETAP ADA** untuk sinyal BUY: SL = close − 2×ATR14, TP1 = 2R, TP2 = 3R,
  dibulatkan ke bawah sesuai fraksi harga BEI. Sinyal SELL menampilkan trailing stop Illu Trx.
- Notifikasi: **satu baris per saham per candle** (sinyal ganda digabung, mis. `🔷COMBO + 📶STRONG`),
  dikelompokkan per arah BUY/SELL, urut: jumlah sinyal → sinyal terkuat → rasio volume. Nama saham = link TradingView.
  Pesan lanjutan hanya memakai baris pertama header (blok status tidak diulang). Legenda singkatan di akhir.

## Struktur kode

| File | Isi |
|---|---|
| `macroflow/indicators.py` | EMA/RMA (seed SMA seperti Pine), RSI, ATR, DMI/ADX, MACD, Stoch, crossover, `illu_trx` (UT Bot) — sudah dicocokkan dengan TA-Lib (selisih < 1e-12); `heikin_ashi()` |
| `macroflow/signals.py` | Pembentukan H4 (09–13 & 13–17 WIB, pola TradingView IDX), D parsial, W; `meter()`; `evaluate()` → list `Signal`; `sl_tp()` |
| `macroflow/data.py` | Daftar saham (TradingView scanner), download Yahoo per 80 ticker, `YAHOO_SYMBOLS` untuk indeks |
| `macroflow/notify.py` | Format HTML Telegram, `merge_signals()` gabung sinyal ganda, split 3900 char, `TV_SYMBOLS` untuk indeks |
| `macroflow/market.py` | `regime()`, `weekly_bars()`, `closed_weeks()`, `signal_events()` (1 Event per candle, D/W), `ihsg_status(daily, now, p, ..., final_upto)` → `MarketStatus`, `fmt_index()` (6,712.45) |
| `macroflow/smc.py` | SMC: `detect()` → `SMC` (choch/bos/ob/fvg buy/sell arrays + `zones`) |
| `macroflow/index_alert.py` | Alert indeks D & W: `specs/register/index_params/final_upto/index_signals/run` |
| `macroflow/study.py` | `ihsg-study`: per hari as-of close hitung kumpulan sinyal (D+W, Meter SMA) & rezim, ret 5/10/20h ke depan, episode, Excel 5 sheet + teks Telegram. IHSG D 10y |
| `macroflow/main.py` | CLI: `auto`, `run <mode>`, `check TICKER --tf D/1H/W` (indeks: SMA, D/W saja), `backtest --start [--end] [--tf D 1H]` (tanggal divalidasi, exit 1 kalau salah), `ihsg-study [--telegram]`, `test-telegram` (diagnostik 3 langkah, exit 1 kalau gagal), `chat-id` |
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
- **Selain tes smoke, jalankan `python tests/yahoo_sim.py <perintah>`** (yfinance palsu berbentuk data Yahoo asli:
  index bernama Date/Datetime, tz Asia/Jakarta, MultiIndex, baris NaN). Bug 19 Okt (`ihsg-study` KeyError 'Tanggal'
  karena index bernama "Date") lolos tes smoke tapi tertangkap simulasi ini. Hapus state.json/*.xlsx sesudahnya.
- GitHub API (gh/api.github.com) diblokir proxy sesi → log run Actions tidak bisa dibaca; minta pengguna tempel log.
- `raw.githubusercontent.com` bisa diakses → cek isi repo public dengan membandingkan file lokal vs raw.
- Push ke GitHub hanya bisa kalau repo dilampirkan sebagai sumber sesi **atau** pengguna memberi PAT di chat.
  Pakai PAT hanya lewat `git -c http.extraHeader=...` per perintah — jangan simpan di `.git/config`/file,
  dan sarankan pengguna mencabut token setelah sesi. Kalau tidak ada akses, kirim file pengganti dan pandu
  pengguna edit lewat `github.com/fyercz/macroflow-alert/edit/main/<path>`.
- **Jangan pernah** menulis token Telegram ke file mana pun. Token & chat ID hanya di
  GitHub Secrets `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`. Bot: @menanglanekalahgamebot.
- Versi action Node 24: checkout@v5, setup-python@v6, cache@v5, upload-artifact@v6.

## Status terakhir (19 Okt 2026)

- `tests/test_smoke.py` & `CLAUDE.md` ditambahkan ke repo (26 tes, ±30 detik, jalan tanpa pytest:
  `python tests/test_smoke.py`). Tes tidak dijalankan di GitHub Actions (hanya lokal).
- Repo terpasang, secret terisi, `test-telegram` **berhasil**. Jadwal otomatis mulai Senin 19 Okt 2026.
- Backtest pertama (data asli, 14–16 Okt 2026, 314 saham) sudah dianalisis — **tidak representatif**:
  periode itu crash IHSG (16 Okt −11,57% ke 5.185, trading halt, isu MSCI). Hasil: 477 sinyal D + 1.818 sinyal 1H
  dalam 3 hari (terlalu ramai, sering 2–4 sinyal di candle yg sama); hampir semua BUY kena SL (win rate 0–11%),
  SELL 1H benar 54–67%. Daily belum punya data 5 bar.
- Menu Run workflow sekarang punya input backtest `start` (default 2026-04-01), `end` (kosong = hari ini),
  `tf` (D dan 1H / D / 1H); input dilewatkan via env (aman dari injeksi). Timeout job backtest 180 menit
  (lainnya 50). Estimasi: ±6 detik/saham untuk 6 bulan D+1H (1H dominan) → ±30–60 menit untuk ~314 saham.
- Backtest kedua **1 Apr–30 Sep 2026** (295 saham; 9.666 sinyal D ≈ 80/hari, 65.904 sinyal 1H ≈ 545/hari).
  Analisis (biaya 0,4% PP, 1 trade per saham/candle, R = jarak SL; median SL D ±10–15%, 1H ±3,8%):
  * BUY secara keseluruhan ≈ impas: D −0,06R, 1H 0,00R (exit TP1). Hit TP1 ±33–39% ≈ acak untuk 2R:1R.
  * Hasil didominasi kondisi pasar: Jun–Agu bagus (+0,2…+0,6R), Apr–Mei & Sep jelek (−0,3…−0,8R).
  * Terbaik (tipis): 1H COMBO/METER +0,05R (TP1) / +0,11R (TP2); D RSI>60 & Vol>3x +0,11R (±3/hari, rawan overfit).
  * Terburuk: GOLDEN D −0,37R net; BUY dengan RSI<50 jelek. ROYAL tidak lebih baik dari yang lain.
  * SELL D COMBO benar 57% (5 bar), 60% bila IHSG Illu Trx SELL → berguna sebagai peringatan.
  * Filter IHSG (Illu Trx) & breadth: membantu sedikit di D (−0,14 → +0,03R), tidak membantu di 1H.
  Script analisis tidak disimpan di repo (dibuat ulang dari Excel bila perlu).
- **Diterapkan 19 Okt 2026**: GOLDEN mati, 1H BUY hanya COMBO+METER, gabung sinyal ganda, status IHSG +
  peringatan keras STRONG SELL ≥2× dalam 5 hari bursa. Belum: filter BUY otomatis berdasarkan IHSG (sengaja tidak,
  backtest hanya membantu sedikit). Pengguna dianjurkan memperlakukan sinyal sebagai watchlist.

## Ide lanjutan yang pernah ditawarkan (belum dikerjakan)

- Input tanggal backtest `5d` = 5 hari bursa terakhir otomatis.
- Kurangi kebisingan ROYAL/METER di 1H (mis. cooldown, atau hapus dari `signals.1H`) setelah lihat hasil backtest.
- Ganti sumber data (EODHD $29.99/bln intraday — cek dulu dukungan `.JK` 1h; Invezgo lokal) bila Yahoo bermasalah;
  cukup ganti `data.download()`.
