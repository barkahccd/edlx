# 🛬 Edel Runway Bot

Bot otomatis untuk **Listing Calls** di [runway.edel.finance](https://runway.edel.finance) — jalan terus 24/7 tanpa cron, auto-submit picks, dengan notifikasi & update cookie via Telegram.

## ✨ Fitur

| Fitur | Keterangan |
|---|---|
| **Auto daemon** | Jalan terus, polling tiap 30 detik (bisa diatur) |
| **Auto-detect round** | Langsung start & submit begitu window buka |
| **Auto-refresh cookie** | Buka Chrome otomatis + baca cookie baru dari Chrome lokal |
| **Telegram 2 arah** | Notif saat ada masalah + kirim cookie baru langsung lewat chat |
| **Real expiry detection** | Decode token session untuk tahu PERSIS kapan expired, bukan tebakan |
| **Warning bertingkat** | >2 jam: aman · <2 jam: warning · <30 menit: mendesak (tiap 5 menit) |
| **History tracking** | Rekam tiap pick & hasil settle ke `history.jsonl` |
| **Logs harian** | Tersimpan rapi di folder `logs/`, append (tidak ketimpa) |

---

## ⚠️ Catatan Penting

Login di Edel Runway Desk pakai **passkey** (Face ID / Touch ID / fingerprint). Ini **tidak bisa** diotomasi sepenuhnya — itu memang cara kerja passkey untuk keamanan. Jadi tiap beberapa jam, kamu tetap perlu login ulang manual. Bot ini bikin proses itu **secepat dan seringan mungkin**:

- Notif Telegram sebelum & saat cookie expired
- Chrome laptop kebuka otomatis untuk login cepat
- Atau cukup kirim cookie baru lewat chat Telegram dari device manapun

---

## 🚀 Setup

### 1. Extract & install dependencies

Extract file `.zip` ini ke folder pilihan kamu, lalu buka Terminal di folder itu:

```bash
cd edel-runway-bot
pip install -r requirements.txt
```

### 2. Buat akun di Edel Runway Desk

Daftar/login dulu di [runway.edel.finance/register](https://runway.edel.finance/register) kalau belum punya akun.

### 3. Konfigurasi `.env`

```bash
cp .env.example .env
```

Edit `.env`, isi minimal:
```
RUNWAY_EMAIL=emailkamu@gmail.com
EDEL_COOKIE=edel_session=ISI_DARI_BROWSER
```

**Cara ambil `EDEL_COOKIE`:**
1. Login di runway.edel.finance
2. Tekan `F12` → tab **Application** → **Cookies** → `runway.edel.finance`
3. Cari baris `edel_session` → copy **Value**
4. Paste ke `.env`: `EDEL_COOKIE=edel_session=<value yang dicopy>`

### 4. Setup Telegram (sangat direkomendasikan)

1. Chat [@BotFather](https://t.me/BotFather) → `/newbot` → ikuti instruksi → catat **token**
2. Chat bot kamu yang baru dibuat → kirim apa saja (misal `/start`)
3. Buka di browser: `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Cari `"chat":{"id": ...}` → itu **chat_id** kamu

Isi ke `.env`:
```
TELEGRAM_TOKEN=1234567890:ABCdef...
TELEGRAM_CHAT_ID=987654321
```

### 5. Jalankan

```bash
python3 runway_bot.py
```

Stop dengan `Ctrl+C`.

---

## 📲 Update Cookie Lewat Telegram (saat expired)

Begitu dapat notif **"🔴 Cookie expired"**:

1. Login ulang di browser manapun (laptop/HP) di runway.edel.finance
2. Ambil cookie baru (F12 → Application → Cookies → `edel_session` → copy value)
3. Kirim ke chat Telegram bot kamu, format:
   ```
   edel_session=NILAI_COOKIE_BARU
   ```
4. Bot otomatis validasi & lanjut jalan — tidak perlu restart, tidak perlu SSH/akses laptop.

> Kalau kamu sedang di laptop yang sama tempat bot jalan, cukup login di Chrome yang sudah dibuka otomatis oleh bot — itu juga akan terdeteksi otomatis tanpa perlu kirim apa pun ke Telegram.

---

## ⚙️ Strategi Pick

Default: pilih saham dengan **market cap lebih besar** di tiap head-to-head. Daftar prioritas ada di `LARGE_CAPS` dalam `runway_bot.py` — bisa diedit bebas.

> Catatan jujur: belum ada bukti pasti bahwa market cap adalah strategi optimal untuk menang di sistem Demand Index Edel — mekanisme scoring asli tidak dipublikasikan. Gunakan `analyze_history.py` setelah terkumpul cukup data untuk evaluasi berbasis hasil nyata, bukan asumsi.

---

## 📊 Analisis Hasil

Setelah bot jalan beberapa hari dan ada round yang `SETTLED`:

```bash
python3 analyze_history.py
```

Menampilkan win-rate per ticker berdasarkan data yang sudah terkumpul.

> Field hasil settlement dari API belum 100% dikonfirmasi strukturnya — kalau output menunjukkan banyak `unknown`, berarti perlu penyesuaian di `find_settlement_result()` sesuai struktur response API yang sebenarnya.

---

## 📁 Struktur File

```
.
├── runway_bot.py         ← Bot utama (jalankan ini)
├── cookie_refresher.py   ← Baca cookie dari Chrome lokal
├── analyze_history.py    ← Analisis win-rate dari history
├── requirements.txt
├── .env                  ← Konfigurasi pribadi (JANGAN di-commit/share)
├── .env.example          ← Template konfigurasi
├── .gitignore
├── .edel_state.json      ← State internal (auto-generated)
├── history.jsonl         ← Riwayat pick & hasil (auto-generated)
└── logs/
    └── bot_YYYYMMDD.log  ← Log harian (auto-generated)
```

---

## 🔄 Jalankan di Background (opsional)

```bash
# macOS/Linux — pakai screen
screen -S edel
python3 runway_bot.py
# Ctrl+A lalu D untuk detach (bot tetap jalan)
# screen -r edel untuk kembali lihat
```

---

## ❓ FAQ

**Q: Kenapa harus login manual tiap beberapa jam?**
A: Passkey (Face ID/Touch ID) tidak bisa diotomasi — itu memang desain keamanannya. Tidak ada workaround.

**Q: Kirim cookie ke Telegram bot aman?**
A: Bot kamu sendiri yang menyimpannya secara lokal di `.env`/`accounts.json` di komputer kamu, tidak dikirim ke server pihak ketiga manapun selain Telegram API (untuk kirim/terima pesan) dan API resmi Edel.

**Q: Kenapa `browser_cookie3` gagal baca cookie?**
A: Cookie `edel_session` di Edel itu **HttpOnly** — sengaja tidak bisa dibaca lewat JavaScript browser biasa (mencegah pencurian via XSS). `browser_cookie3` tetap bisa baca karena dia akses database SQLite Chrome langsung dari disk, bukan lewat JavaScript. Pastikan permission Keychain (macOS) sudah di-approve saat diminta.

**Q: Strategi market cap itu beneran efektif?**
A: Belum terbukti. Itu cuma heuristik awal. Pakai `analyze_history.py` untuk evaluasi berbasis data nyata setelah cukup banyak round terkumpul.

---

## 📜 Disclaimer

Bot ini dibuat untuk keperluan eksperimen pribadi. Penggunaan automasi pada platform Edel Finance Runway Desk sepenuhnya menjadi risiko dan tanggung jawab masing-masing pengguna. Pastikan untuk membaca Terms of Service Edel sebelum menggunakan automasi semacam ini.
