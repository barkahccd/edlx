"""
cookie_refresher.py
━━━━━━━━━━━━━━━━━━
Baca cookie edel_session langsung dari Chrome lokal (browser_cookie3),
dan buka Chrome ke runway.edel.finance secara otomatis.

Dipanggil oleh runway_bot.py saat cookie expired — bot akan polling
fungsi read_cookie() sampai ketemu cookie baru yang valid.
"""

import subprocess
from pathlib import Path

COOKIE_NAME = "edel_session"
BASE_URL = "https://runway.edel.finance"
LOGIN_URL = "https://runway.edel.finance/login"


def save_env_value(key: str, value: str):
    """Update satu key di .env tanpa menghapus baris lain"""
    env_path = Path(__file__).parent / ".env"
    lines = []
    found = False

    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)

    if not found:
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def open_chrome():
    """Buka Chrome langsung ke halaman login Edel Runway (macOS)"""
    subprocess.Popen(["open", "-a", "Google Chrome", LOGIN_URL])


def read_cookie():
    """
    Baca cookie edel_session langsung dari Chrome lokal.
    Bisa baca HttpOnly cookie karena akses langsung ke database SQLite
    Chrome di disk, bukan lewat JavaScript.

    Catatan: di macOS, browser_cookie3 perlu izin akses Keychain —
    akan muncul popup permintaan password sistem saat pertama dipanggil.
    """
    try:
        import browser_cookie3

        cookies = browser_cookie3.chrome(domain_name="runway.edel.finance")
        for c in cookies:
            if c.name == COOKIE_NAME:
                return f"{COOKIE_NAME}={c.value}"

        cookies = browser_cookie3.chrome(domain_name=".edel.finance")
        for c in cookies:
            if c.name == COOKIE_NAME:
                return f"{COOKIE_NAME}={c.value}"

        return None

    except Exception as e:
        print(f"[cookie_refresher] browser_cookie3 error: {e}")
        return None


if __name__ == "__main__":
    # Test manual
    print("[cookie_refresher] Membuka Chrome...")
    open_chrome()
    print("[cookie_refresher] Login dulu, lalu tekan Enter untuk cek cookie...")
    input()

    cookie = read_cookie()
    if cookie:
        print(f"✅ Cookie ditemukan: {cookie[:50]}...")
    else:
        print("❌ Cookie tidak ditemukan")