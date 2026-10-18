# MacroFlow V65 Alert → Telegram

Notifikasi sinyal BUY/SELL dari indikator **MacroFlow V65** (Pine Script) untuk semua saham IDX.
Sinyal dikirim ke Telegram dan dijalankan otomatis gratis di GitHub Actions.

## Sinyal yang dikirim

| Sinyal | Kondisi (sama dengan Pine) |
|---|---|
| 👑 ROYAL Buy/Sell | Meter H4, Daily, dan Weekly semuanya BUY (atau semuanya SELL), plus volume > SMA20 volume dan candle hijau (atau merah) |
| 🔷 COMBO Buy/Sell | Illu Trx uptrend + close > EMA50 + MACD > signal + RSI > 50 (kebalikannya untuk Sell) |
| 🌟 GOLDEN SETUP | Close > EMA200 dan Stoch %K cross up %D saat %K < 30 |
| 📍 STANDAR Buy/Sell | Illu Trx berbalik arah (Key 2, ATR 10) |
| 📶 METER STRONG | Meter timeframe chart berubah menjadi STRONG BUY / STRONG SELL |

Setiap sinyal disertai harga, % perubahan, RSI, dan rasio volume. Sinyal BUY ditambah
SL = close − 2×ATR14, TP1 = 2R, TP2 = 3R (dibulatkan ke fraksi harga BEI). Sinyal SELL ditambah level trailing stop Illu Trx.

**Yang aktif saat ini** (berdasarkan backtest 1 Apr–30 Sep 2026, bisa diubah di `config.yaml`):

| Timeframe | BUY | SELL |
|---|---|---|
| Daily | ROYAL, COMBO, STANDAR, METER | ROYAL, COMBO, STANDAR, METER |
| 1H | COMBO, METER | ROYAL, COMBO, STANDAR, METER |

GOLDEN dimatikan (hasil backtest paling jelek). Kalau satu saham mendapat beberapa sinyal di candle yang sama,
semuanya digabung jadi satu baris, misalnya `🔷COMBO + 📶STRONG`. Saham dengan sinyal terbanyak tampil paling atas.

## Status IHSG & peringatan keras

Setiap notifikasi diawali **status IHSG**:
- arah (🟢 NAIK / 🔴 TURUN / 🟡 CAMPURAN) dari Illu Trx & Meter Daily, harga, % perubahan, posisi terhadap EMA50;
- **rezim EMA200**: 🟢 UPTREND (harga di atas EMA200 yang naik), 🔴 DOWNTREND (di bawah EMA200 yang turun),
  atau 🟡 TRANSISI. Dari chart IHSG 2025–2026: saat UPTREND sinyal SELL tunggal sering jebakan,
  saat DOWNTREND sinyal BUY biasanya hanya pantulan;
- jumlah sinyal SELL & BUY IHSG dalam 5 hari bursa terakhir.

**🚨 PERINGATAN KERAS** muncul bila IHSG mendapat sinyal SELL (COMBO, STANDAR, atau STRONG SELL) di
**2 candle berbeda atau lebih dalam 5 hari bursa terakhir** (Daily & 1H dihitung). Peringatan dikirim sebagai
pesan tersendiri (sekali per hari) dan banner di atas setiap notifikasi hari itu. Kalau ada ≥2 sinyal BUY
saat UPTREND, status menampilkan **✅ Konfirmasi tren naik**. Semua angka bisa diubah di `market_warning`.

### Menguji pola dengan data asli (`ihsg-study`)
Actions → MacroFlow Alert → Run workflow → pilih **`ihsg-study`** (±2 menit). Ringkasan dikirim ke Telegram dan
file Excel **ihsg-study** bisa diunduh di halaman run. Isinya: untuk setiap aturan (STRONG SELL ≥2, semua SELL ≥2/≥3,
Daily saja, + DOWNTREND, BUY + UPTREND, dll.) berapa kali muncul dan apa yang terjadi pada IHSG 5/10/20 hari
sesudahnya dibanding hari biasa, serta seberapa sering sinyal IHSG benar menurut rezim EMA200.
Data: IHSG harian 10 tahun, 1 jam ±2 tahun (batas Yahoo).

## Jadwal (WIB, Senin–Jumat)

| Jam | Isi |
|---|---|
| 10:15, 11:15, 12:15, 14:15, 15:15 | Sinyal **1H** dari candle 1 jam yang baru close |
| 14:45 | Sinyal **Daily SEMENTARA** (candle hari ini belum close, masih sempat masuk di pre-closing) |
| 16:15 | Sinyal 1H candle terakhir + **Daily FINAL**, termasuk daftar sinyal sementara yang batal |

Hari libur bursa terdeteksi otomatis (tidak ada data hari itu, jadi tidak ada notifikasi).
Jadwal GitHub kadang telat 5–15 menit, dan itu masih aman karena datanya memang delay.

---

## Setup (±15 menit)

### 1. Buat bot Telegram
1. Di Telegram, buka **@BotFather**, kirim `/newbot`, lalu ikuti langkahnya. Simpan **token** yang diberikan
   (bentuknya `123456789:ABC...`).
2. Buka chat dengan bot baru kamu dan kirim pesan apa saja (misalnya `halo`).
3. Buka di browser: `https://api.telegram.org/bot<TOKEN>/getUpdates`. Cari `"chat":{"id":123456789`.
   Angka itu adalah **chat ID** kamu.
   *(Kalau mau dikirim ke grup: masukkan bot ke grup, kirim pesan di grup, lalu ulangi langkah ini.
   ID grup diawali tanda minus.)*

### 2. Upload ke GitHub
1. Buat repository baru di github.com dan pilih **Private**.
2. Upload semua isi folder ini (termasuk folder `.github`).
3. Buka **Settings → Secrets and variables → Actions → New repository secret**, lalu buat dua secret:
   - `TELEGRAM_BOT_TOKEN` = token dari BotFather
   - `TELEGRAM_CHAT_ID` = chat ID
4. Buka tab **Actions**, pilih **MacroFlow Alert**, klik **Run workflow**, pilih `test-telegram`, lalu jalankan.
   Kalau berhasil, pesan "MacroFlow Alert terhubung" akan masuk ke Telegram.
5. Selesai. Mulai hari bursa berikutnya, jadwal berjalan otomatis.

Pemakaian: sekitar 7 run per hari × 3–5 menit, masih dalam kuota gratis GitHub (2.000 menit/bulan untuk repo private).

---

## Data feed

Data diambil otomatis dari **Yahoo Finance** lewat library Python `yfinance`. Tidak perlu daftar
atau API key. Kode saham IDX di Yahoo memakai akhiran `.JK` (contoh `BBCA.JK`), dan app ini
menambahkannya sendiri. Daftar semua saham IDX diambil dari screener publik TradingView
dan disimpan ke `tickers.txt` (diperbarui tiap 7 hari).

Catatan: Yahoo gratis untuk pemakaian pribadi, tetapi bukan API resmi. Sesekali Yahoo membatasi akses
(rate limit) atau formatnya berubah, sehingga `yfinance` perlu di-update (`pip install -U yfinance`).
App ini sudah download per kelompok dan otomatis mencoba ulang.

## Mengatur (`config.yaml`)

- `tickers`: biarkan `[]` untuk semua saham IDX, atau isi daftar sendiri.
- `indices`: indeks yang selalu dicek dan tampil paling atas di pesan. Default IHSG (`^JKSE`), dengan semua sinyal
  kecuali ROYAL (volume indeks di Yahoo tidak akurat). Cek riwayatnya: `python -m macroflow.main check IHSG`.
- `filter.min_avg_value`: saham dengan rata-rata nilai transaksi 20 hari di bawah angka ini tidak dicek
  (default Rp 2 miliar). Naikkan kalau notifikasinya terlalu banyak.
- `signals`: jenis sinyal per timeframe dan arah (`BUY` / `SELL`). Hapus yang tidak perlu. Bisa juga ditulis
  singkat untuk kedua arah, misalnya `1H: [COMBO, METER]`.
- `market_warning`: indeks acuan, jumlah hari bursa yang dilihat (`window_days`), jumlah candle bersinyal SELL
  untuk peringatan keras (`min_events`), jenis sinyal yang dihitung (`signals`), dan timeframe (`timeframes`).
- `params`: parameter indikator, sama dengan input di Pine Script.

## Mencocokkan dengan TradingView

Jalankan di komputer (butuh Python 3.10+):

```bash
pip install -r requirements.txt
python -m macroflow.main check BBCA --tf D --bars 120
python -m macroflow.main check BBCA --tf 1H --bars 60
```

Perintah ini menampilkan riwayat sinyal. Bandingkan dengan panah/label di chart TradingView.

Perbedaan kecil yang mungkin muncul:
- **Sumber data**: Yahoo Finance, bukan feed IDX TradingView. Kadang ada selisih OHLC/volume kecil,
  terutama di saham tidak likuid.
- **H4 (untuk ROYAL)**: dibentuk dari data 1 jam dengan pola TradingView (09:00–13:00 dan 13:00–sesi 2).
- **ROYAL di histori**: dihitung seperti saat realtime (memakai candle H4/D/W yang sedang berjalan),
  jadi sama dengan yang muncul saat live. Di chart historis TradingView kadang tampil sedikit berbeda
  karena `request.security` me-repaint bar lama.
- Hanya candle yang **sudah close** yang dipakai untuk 1H, jadi sinyal 1H tidak repaint.

## Backtest

Putar ulang aplikasi untuk periode yang sudah lewat, lalu lihat hasil setiap sinyal.

**Lewat GitHub (tanpa install apa-apa):** buka tab Actions → MacroFlow Alert → Run workflow. Pilih `backtest`,
isi tanggal mulai, tanggal akhir (kosongkan = sampai hari ini), dan timeframe (`D dan 1H`, `D`, atau `1H`),
lalu jalankan. Untuk 6 bulan, `D` saja sekitar 5–10 menit; `D dan 1H` sekitar 30–60 menit. Ringkasan dikirim ke Telegram,
dan file Excel lengkap bisa diunduh di halaman run tersebut (bagian **Artifacts → backtest-hasil**).

**Lewat komputer:**
```bash
python -m macroflow.main backtest --start 2026-04-01 --end 2026-09-30   # Daily + 1H
python -m macroflow.main backtest --start 2026-04-01 --tf D             # Daily saja
```

Isi Excel:
- **Ringkasan**: per timeframe & jenis sinyal, berisi jumlah sinyal, win rate BUY (TP1/TP2 tersentuh sebelum SL),
  rata-rata return setelah 5 & 10 candle, dan % arah benar.
- **Sinyal Daily / Sinyal 1H**: setiap sinyal beserta SL, TP1, TP2, hasilnya, return, serta kenaikan/penurunan maksimum.
- **Catatan**: asumsi yang dipakai. Entry di close candle sinyal; kalau SL & TP tersentuh di candle yang sama,
  dianggap SL; belum termasuk fee, slippage, dan ARB/ARA.

## Perintah lain

```bash
python -m macroflow.main auto                 # yang dipanggil jadwal (otomatis sesuai jam)
python -m macroflow.main run daily-final      # paksa mode tertentu (hourly / daily-preclose / daily-final)
python -m macroflow.main run hourly --dry-run # print ke layar, tidak kirim Telegram
python -m macroflow.main test-telegram
python -m macroflow.main ihsg-study           # uji pola IHSG, hasil ihsg_study.xlsx
```

> Ini alat bantu notifikasi, bukan rekomendasi investasi. Tetap cek chart sebelum eksekusi.
