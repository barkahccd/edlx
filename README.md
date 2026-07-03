# 🛬 Edel Runway Bot — Multi Account (v5.8)

Bot otomatis untuk **Listing Calls** di [runway.edel.finance](https://runway.edel.finance) — jalan banyak akun sekaligus dalam 1 proses, dengan dashboard terminal, kontrol per-akun, dan update cookie via Telegram.

## ✨ Fitur

| Fitur | Keterangan |
|---|---|
| **Multi akun paralel** | 1 proses, tiap akun jalan di thread sendiri (baca dari `accounts.json`) |
| **Dashboard terminal (Textual)** | `python3 runway_bot.py` langsung buka tabel live semua akun: session, status round, votes, window, last message |
| **Kontrol per akun** | S/X/R di dashboard cuma ngaruh ke akun yang lagi disorot — akun lain tetap jalan (pakai stop event + stdin control loop per akun) |
| **Auto-detect round & auto-submit** | Deteksi window buka lalu submit pick otomatis, sesuai tahapan asli web (Calls Created → Allocation Pending → Calls Open → Selections Submitted → Demand Index Pending → Demand Index Final) |
| **Telegram 2 arah** | 1 chat buat semua akun — command status/vote + kirim cookie baru per akun langsung dari chat |
| **Real expiry detection** | Decode klaim `e` di token `edel_session` buat tahu PERSIS kapan expired, bukan tebakan. Tier warning: >2 jam aman, <2 jam normal, <30 menit mendesak (reminder tiap 5 menit) |
| **Cookie fallback dari Chrome lokal** | `cookie_refresher.py` bisa baca cookie langsung dari Chrome (via `browser_cookie3`) sebagai jalur tambahan selain Telegram |
| **History per akun** | Tiap akun rekam pick & hasil settle ke `history/<acc_id>.jsonl` |
| **Logs harian** | Tersimpan di `logs/`, append (tidak ketimpa) |

---

## ⚠️ Catatan Penting

Login di Edel Runway Desk pakai **passkey** (Face ID / Touch ID / fingerprint), jadi **tidak bisa** diotomasi penuh — itu memang desain keamanannya. Tiap beberapa jam kamu tetap perlu login ulang manual per akun. Bot ini bikin proses itu secepat mungkin lewat dua jalur:

- Notif Telegram sebelum & saat cookie sebuah akun expired
- Kirim cookie baru lewat chat Telegram dari device manapun (format `@acc1 edel_session=...`)
- Atau, kalau lagi di laptop yang sama, `cookie_refresher.py` bisa baca ulang cookie langsung dari Chrome

---

## 📁 Struktur File

```
.
├── runway_bot.py          ← Manager utama (jalankan ini) — dashboard + kontrol multi akun
├── runway_worker.py       ← Logic worker per akun (di-load ulang oleh runway_bot.py untuk tiap akun)
├── cookie_refresher.py    ← Baca cookie edel_session dari Chrome lokal (fallback)
├── analyze_history.py     ← Analisis win-rate dari history.jsonl (mode 1 akun / legacy)
├── accounts.json          ← Daftar akun (id, email, cookie awal opsional) — WAJIB diisi
├── requirements.txt
├── .env                   ← Konfigurasi pribadi (JANGAN di-commit/share)
├── .env.example           ← Template konfigurasi
├── .gitignore
├── sessions/
│   └── <acc_id>.json      ← Cookie/session tersimpan per akun (auto-generated)
├── states/
│   └── <acc_id>.json      ← State internal per akun — round terakhir, dll (auto-generated)
├── history/
│   └── <acc_id>.jsonl     ← Riwayat pick & hasil settle per akun (auto-generated)
└── logs/
    └── bot_YYYYMMDD.log   ← Log harian gabungan (auto-generated)
```

---

## 🚀 Setup

### 1. Extract & install dependencies

```bash
cd edel-runway-bot
python3 -m pip install -r requirements.txt
```

### 2. Isi `accounts.json`

Format simpel, 3 baris per akun (id / email / cookie awal — cookie boleh dikosongi dan diisi belakangan via Telegram):

```
acc1
akun1@gmail.com
eyJ...

acc2
akun2@gmail.com
eyJ...

acc3
akun3@gmail.com
```

### 3. Konfigurasi `.env`

```bash
cp .env.example .env
```

Isi minimal token & chat ID Telegram (opsional tapi sangat direkomendasikan untuk multi akun):

```
TELEGRAM_TOKEN=1234567890:ABCdef...
TELEGRAM_CHAT_ID=987654321
POLL_INTERVAL=30
```

**Cara ambil token & chat ID:**
1. Chat [@BotFather](https://t.me/BotFather) → `/newbot` → catat **token**
2. Chat bot barumu → kirim apa saja (misal `/start`)
3. Buka di browser: `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Cari `"chat":{"id": ...}` → itu **chat_id** kamu

### 4. Jalankan

```bash
python3 runway_bot.py
```

Ini langsung buka **dashboard terminal** (Textual) yang nampilin semua akun. Kalau mau mode log biasa (tanpa UI, misal buat jalan di background via `screen`):

```bash
python3 runway_bot.py --plain
```

Stop dengan `Ctrl+C` (atau `Q` di dashboard).

---

## 🖥️ Kontrol Dashboard

| Key | Aksi |
|---|---|
| `S` | Start akun yang lagi disorot |
| `X` | Stop akun yang lagi disorot |
| `R` | Restart akun yang lagi disorot (akun lain **tidak** ikut restart) |
| `Shift+R` | Restart **semua** akun |
| `A` | Tampilkan log gabungan semua akun |
| `C` | Clear log yang lagi ditampilkan |
| `Q` | Quit |

> Kalau kamu perhatikan akun lain ikut kena efek pas restart 1 akun — itu bug lama yang sudah diperbaiki lewat stop event + kontrol per akun sendiri-sendiri. Kalau masih kejadian di versi kamu, kemungkinan besar `runway_bot.py`/`runway_worker.py` belum versi terbaru.

---

### Update cookie per akun

```
@acc1 eyJ...
@acc1 edel_session=eyJ...
```

Alurnya:
1. Login ulang di browser manapun (laptop/HP) di runway.edel.finance
2. Ambil cookie baru: `F12` → **Application** → **Cookies** → cari `edel_session` → copy **Value**
3. Kirim ke chat Telegram bot dengan prefix akun, contoh: `@acc1 edel_session=NILAI_COOKIE_BARU`
4. Bot otomatis validasi ke API asli dulu sebelum disimpan ke `sessions/acc1.json` — kalau valid, worker lanjut jalan tanpa perlu restart manual

> Kalau cuma ada 1 akun terdaftar, kirim cookie tanpa prefix `@acc1` juga tetap kedeteksi otomatis.

---

## ⚙️ Strategi Pick

Default: pilih saham dengan **market cap lebih besar** di tiap head-to-head. Daftar prioritas ada di `LARGE_CAPS` dalam `runway_worker.py` — bisa diedit bebas.

> Catatan jujur: belum ada bukti pasti market cap adalah strategi optimal untuk menang di sistem Demand Index Edel — mekanisme scoring asli tidak dipublikasikan. Pakai `analyze_history.py` setelah data cukup buat evaluasi berbasis hasil nyata, bukan asumsi.

---

## 📊 Analisis Hasil

```bash
python3 analyze_history.py
```

Menampilkan win-rate per ticker dari `history.jsonl` di root folder. **Catatan:** di mode multi akun, tiap akun nyimpen history-nya sendiri-sendiri di `history/<acc_id>.jsonl` — script ini belum otomatis menggabungkan semuanya, jadi kalau mau analisis per akun, arahkan `HISTORY_FILE` di `analyze_history.py` ke file akun yang dimaksud.

---

## 🔄 Jalankan di Background (opsional)

```bash
# macOS/Linux — pakai screen
screen -S edel
python3 runway_bot.py --plain
# Ctrl+A lalu D untuk detach (bot tetap jalan)
# screen -r edel untuk kembali lihat
```

---

## 🐞 Known Issues

- **Cookie kadang kebaca "expired" terus abis restart** — lagi diselidiki, kemungkinan state expiry belum ke-refresh bersih pas worker akun di-restart, jadi sempat spam notif Telegram walau cookie sebenarnya masih valid. Workaround sementara: cek `/status acc1` manual buat mastiin sebelum kirim ulang cookie.
- `analyze_history.py` belum aware ke struktur `history/<acc_id>.jsonl` multi akun (lihat bagian Analisis Hasil di atas).

---

## ❓ FAQ

**Q: Kenapa harus login manual tiap beberapa jam per akun?**
A: Passkey (Face ID/Touch ID) tidak bisa diotomasi — itu memang desain keamanannya. Tidak ada workaround.

**Q: Kirim cookie ke Telegram bot aman?**
A: Bot kamu sendiri yang menyimpannya secara lokal per akun di `sessions/<acc_id>.json` di komputer kamu, tidak dikirim ke server pihak ketiga manapun selain Telegram API (kirim/terima pesan) dan API resmi Edel.

**Q: Kenapa `browser_cookie3` gagal baca cookie?**
A: Cookie `edel_session` itu **HttpOnly** — sengaja tidak bisa dibaca lewat JavaScript browser biasa (mencegah pencurian via XSS). `browser_cookie3` tetap bisa baca karena akses database SQLite Chrome langsung dari disk. Pastikan permission Keychain (macOS) sudah di-approve saat diminta.

**Q: Restart 1 akun kok akun lain ikut kepengaruh?**
A: Sudah diperbaiki lewat stop event + stdin control loop per akun (lihat bagian Kontrol Dashboard). Kalau masih kejadian, pastikan pakai versi `runway_bot.py` terbaru.

**Q: Strategi market cap itu beneran efektif?**
A: Belum terbukti, itu cuma heuristik awal. Pakai `analyze_history.py` untuk evaluasi berbasis data nyata setelah cukup banyak round terkumpul.

---

## 📜 Disclaimer

Bot ini dibuat untuk keperluan eksperimen pribadi. Penggunaan automasi pada platform Edel Finance Runway Desk sepenuhnya menjadi risiko dan tanggung jawab masing-masing pengguna. Pastikan membaca Terms of Service Edel sebelum menggunakan automasi semacam ini.