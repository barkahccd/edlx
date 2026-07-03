#!/usr/bin/env python3
"""
Edel Runway Desk Automation v5.7
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Auto-loop daemon
✅ Auto-detect round
✅ Auto-submit picks
✅ Logs append harian, tidak reset
✅ Settlement log hemat anti-spam
✅ EDELx staked/locked per round (dari field stakeAmount di /listing-rounds/current)
✅ Log lebih ringkas — satu baris per event, emoji minim
✅ History tracking (history.jsonl) — rekam pick & hasil settle per round
✅ Fix: restart sempat diam total kalau throttle state lama masih match
✅ Fix: restart saat window OPEN sekarang langsung submit, tidak nunggu tick ke-2
✅ Fix: log alasan kalau start/submit belum enabled (sebelumnya diam total)
✅ Cookie refresh: buka Chrome otomatis + tunggu tanpa batas waktu kaku + validasi
   ke API asli (bukan cuma 'ada di Chrome') + notifikasi Telegram (awal/reminder/sukses)
✅ Telegram 2-arah: kirim cookie/token via chat untuk update tanpa SSH/laptop
✅ Real expiry detection: decode token edel_session (klaim 'e') untuk tahu PERSIS
   kapan expired, bukan estimasi. Tier warning: >2j aman, <2j normal, <30m mendesak/5m
✅ Ctrl+C clean stop
"""

import json, time, os, sys, logging, traceback, base64
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
ACCOUNT_ID = os.environ.get("EDEL_ACCOUNT_ID", "default").strip() or "default"
ACCOUNT_EMAIL = os.environ.get("EDEL_ACCOUNT_EMAIL", "").strip()
MULTI_ACCOUNT_MODE = os.environ.get("EDEL_MULTI_ACCOUNT_MODE", "0").strip() == "1"
WORKER_TELEGRAM_POLL = os.environ.get("EDEL_WORKER_TELEGRAM_POLL", "1").strip() != "0"

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"bot_{datetime.now().strftime('%Y%m%d')}.log"

STATE_DIR = BASE_DIR / "states"
HISTORY_DIR = BASE_DIR / "history"
SESSION_DIR = BASE_DIR / "sessions"
if MULTI_ACCOUNT_MODE:
    STATE_DIR.mkdir(exist_ok=True)
    HISTORY_DIR.mkdir(exist_ok=True)

STATE_FILE = (STATE_DIR / f"{ACCOUNT_ID}.json") if MULTI_ACCOUNT_MODE else (BASE_DIR / ".edel_state.json")
HISTORY_FILE = (HISTORY_DIR / f"{ACCOUNT_ID}.jsonl") if MULTI_ACCOUNT_MODE else (BASE_DIR / "history.jsonl")
SESSION_FILE = SESSION_DIR / (f"{ACCOUNT_ID}.json" if MULTI_ACCOUNT_MODE else "state.json")

BASE_URL = "https://runway.edel.finance"


class WIBFormatter(logging.Formatter):
    HARI = {
        "Monday": "Senin",
        "Tuesday": "Selasa",
        "Wednesday": "Rabu",
        "Thursday": "Kamis",
        "Friday": "Jumat",
        "Saturday": "Sabtu",
        "Sunday": "Minggu",
    }

    BULAN = {
        "January": "Januari",
        "February": "Februari",
        "March": "Maret",
        "April": "April",
        "May": "Mei",
        "June": "Juni",
        "July": "Juli",
        "August": "Agustus",
        "September": "September",
        "October": "Oktober",
        "November": "November",
        "December": "Desember",
    }

    def formatTime(self, record, datefmt=None):
        wib = datetime.fromtimestamp(record.created, tz=timezone.utc) + timedelta(hours=7)

        hari = self.HARI[wib.strftime("%A")]
        bulan = self.BULAN[wib.strftime("%B")]

        return f"{hari} {wib.day} {bulan} {wib.strftime('%H.%M')} WIB"


class AccountLogFilter(logging.Filter):
    def filter(self, record):
        record.account = ACCOUNT_ID
        return True


def setup_logger():
    logger = logging.getLogger(f"edel.{ACCOUNT_ID}")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    if MULTI_ACCOUNT_MODE:
        fmt = WIBFormatter("%(asctime)s [%(levelname)s] [%(account)s] %(message)s")
    else:
        fmt = WIBFormatter("%(asctime)s [%(levelname)s] %(message)s")

    account_filter = AccountLogFilter()

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    ch.addFilter(account_filter)

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    fh.addFilter(account_filter)

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


log = setup_logger()


def load_env():
    env_path = Path(__file__).parent / ".env"
    env = {}

    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    for k in list(env.keys()):
        if k in os.environ:
            env[k] = os.environ[k]

    return env


def save_env_value(key, value):
    env_path = Path(__file__).parent / ".env"
    lines = []
    found = False

    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)

    if not found:
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


CFG = load_env()


# ── Session file helpers (format: {cookies: [...], origins: [...]}) ──────────

def load_session_file() -> dict | None:
    """Load session dari sessions/state.json (format Playwright-compatible)."""
    try:
        if SESSION_FILE.exists():
            return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.debug(f"Gagal load session file: {e}")
    return None


def save_session_file(state: dict):
    """Simpan session ke sessions/state.json."""
    SESSION_DIR.mkdir(exist_ok=True)
    SESSION_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    log.debug(f"Session tersimpan ke {SESSION_FILE}")


def parse_cookie_string(cookie_str: str, domain: str = "runway.edel.finance") -> list:
    """
    Parse cookie string (name1=val1; name2=val2) jadi list dict Playwright-compatible.
    Juga terima format 'Cookie: ...' dengan prefix.
    """
    cookies = []
    cleaned = cookie_str.strip()
    if cleaned.lower().startswith("cookie:"):
        cleaned = cleaned[7:].strip()

    for pair in cleaned.split(";"):
        eq_idx = pair.find("=")
        if eq_idx == -1:
            continue
        name = pair[:eq_idx].strip()
        value = pair[eq_idx + 1:].strip()
        if not name:
            continue
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "expires": int(time.time()) + 86400 * 30,
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        })
    return cookies


def build_session_state(cookies: list) -> dict:
    return {
        "cookies": cookies,
        "origins": [{"origin": "https://runway.edel.finance", "localStorage": []}],
    }


def get_cookie_header_from_session() -> str | None:
    """Baca session file dan build Cookie header string. Return None kalau tidak ada."""
    session = load_session_file()
    if not session or not session.get("cookies"):
        return None
    return "; ".join(f"{c['name']}={c['value']}" for c in session["cookies"])


def get_edel_session_value() -> str | None:
    """Ambil value dari cookie edel_session saja (untuk decode JWT)."""
    session = load_session_file()
    if not session or not session.get("cookies"):
        if MULTI_ACCOUNT_MODE:
            return None
        # Fallback ke .env
        cfg = load_env()
        raw = cfg.get("EDEL_COOKIE", "")
        if raw.startswith("edel_session="):
            return raw[len("edel_session="):]
        return raw or None
    edel = next((c for c in session["cookies"] if c["name"] == "edel_session"), None)
    if edel:
        return edel["value"]
    return None

RUNWAY_EMAIL = ACCOUNT_EMAIL or CFG.get("RUNWAY_EMAIL", "")
TG_TOKEN = CFG.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID = CFG.get("TELEGRAM_CHAT_ID", "")
POLL_INTERVAL = int(CFG.get("POLL_INTERVAL", "20"))
MAX_API_ERRORS = int(CFG.get("MAX_API_ERRORS", "3"))

# Jarak minimum antar notif "cookie expired" ke Telegram (detik). Disimpan di state file
# supaya tetap berlaku lintas restart — biar restart/stop-start berkali-kali (misal lagi
# testing) gak nge-spam pesan "Cookie Edel expired" yang sama berulang-ulang.
COOKIE_EXPIRED_NOTIFY_COOLDOWN = int(CFG.get("COOKIE_EXPIRED_NOTIFY_COOLDOWN", str(10 * 60)))


def _cookie_expired_notify_allowed() -> bool:
    state = load_state()
    last_notify = float(state.get("cookie_expired_notify_ts") or 0)
    return (time.time() - last_notify) >= COOKIE_EXPIRED_NOTIFY_COOLDOWN


def _mark_cookie_expired_notified():
    state = load_state()
    state["cookie_expired_notify_ts"] = time.time()
    save_state(state)


def _reset_cookie_expired_notify():
    state = load_state()
    state["cookie_expired_notify_ts"] = 0
    save_state(state)


# Berdasarkan HAR: web sering blank / 502 / 504 saat start & submit.
# Jangan langsung gagal, ulangi seperti mekanisme web.
START_RETRY_ATTEMPTS = int(CFG.get("START_RETRY_ATTEMPTS", "6"))
SUBMIT_RETRY_ATTEMPTS = int(CFG.get("SUBMIT_RETRY_ATTEMPTS", "10"))
ACTION_RETRY_DELAY = float(CFG.get("ACTION_RETRY_DELAY", "5"))

# HAR acc4 menunjukkan web bisa menunggu 50-60 detik untuk /balances dan /portfolio.
# Timeout 30 detik terlalu agresif dan bikin bot kelihatan error padahal web masih loading.
API_TIMEOUT = int(CFG.get("API_TIMEOUT", "75"))

# Biar 502/504 dari server Edel tidak spam terminal tiap tick saat state terakhir sudah jelas.
SERVER_BUSY_LOG_COOLDOWN = int(CFG.get("SERVER_BUSY_LOG_COOLDOWN", "180"))

PASSIVE_ROUND_STATUSES = {
    "LOCKED",
    "LOCK_PENDING",
    "DEMAND_INDEX_PENDING",
    "SETTLEMENT_PENDING",
    "SUBMITTED",
}


LARGE_CAPS = {
    "AAPL": 100, "MSFT": 99, "GOOGL": 98, "GOOG": 98, "AMZN": 97,
    "NVDA": 96, "META": 95, "TSLA": 94, "BRK.B": 93, "UNH": 92,
    "JNJ": 91, "V": 90, "JPM": 89, "WMT": 88, "PG": 87,
    "MA": 86, "HD": 85, "CVX": 84, "MRK": 83, "ABBV": 82,
    "LLY": 81, "AVGO": 80, "PEP": 79, "COST": 78, "NFLX": 77,
    "TMO": 76, "ADBE": 75, "CRM": 74, "ACN": 73, "AMD": 72,
    "QCOM": 71, "ORCL": 70, "CSCO": 69, "TXN": 67, "NEE": 66,
    "PM": 65, "UPS": 64, "RTX": 63, "LOW": 62, "HON": 61,
    "AMGN": 60, "SPX": 55, "SPY": 54, "QQQ": 53, "NDX": 52,
    "MU": 51, "XOM": 50, "KLAC": 50, "LRCX": 49, "SNDK": 48,
    "LIN": 47, "KO": 46, "SNPS": 45, "PLTR": 40,
}


PIPELINE_STAGES = [
    ("CREATED", "Calls Created", "📋"),
    ("ALLOCATION_PENDING", "Allocation Pending", "⏳"),
    ("OPEN", "Calls Open", "🟢"),
    ("SUBMITTED", "Selections Submitted", "🔒"),
    ("DEMAND_INDEX_PENDING", "Demand Index Pending", "🔄"),
    ("SETTLED", "Demand Index Final", "🏁"),
]

STATUS_TO_STAGE = {
    "CREATED": 0,
    "ALLOCATION_PENDING": 1,
    "LOCKED": 1,
    "OPEN": 2,
    "SUBMITTED": 3,
    "DEMAND_INDEX_PENDING": 4,
    "SETTLEMENT_PENDING": 4,
    "SETTLED": 5,
    "NO_ROUND": -1,
}


class CookieExpiredError(Exception):
    pass


class Suspected404Error(Exception):
    """
    404 dengan cookie yang ada di session — BISA berarti cookie expired, tapi juga sering
    cuma server Edel glitch sesaat (misal proses/manager baru aja restart, race pas deploy,
    dsb — lihat catatan HAR soal 502/504 juga). Jangan langsung divonis cookie expired dari
    SATU kali 404 doang; biar lewat jalur retry generik dulu (MAX_API_ERRORS) baru dianggap
    cookie beneran expired kalau 404-nya konsisten berkali-kali berturut-turut.
    """
    pass


def safe_dict(v):
    return v if isinstance(v, dict) else {}


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def append_history(record: dict):
    """Tambah satu baris ke history.jsonl (JSON Lines — satu round per baris)"""
    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.debug(f"Gagal nulis history: {e}")


def record_submitted_picks(round_id: str, decisions: list):
    """
    Simpan picks yang baru disubmit ke history, dengan status 'pending'
    (belum tahu hasil menang/kalah, masih nunggu settlement)
    """
    picks = []
    for d in decisions:
        ticker_a = d.get("assetAId", "").replace("asset-", "")
        ticker_b = d.get("assetBId", "").replace("asset-", "")
        picked = (
            d.get("pickedAssetId")
            or d.get("selectedAssetId")
            or d.get("_botPickedAssetId")
            or ""
        ).replace("asset-", "")
        picks.append({
            "decisionId": get_decision_submit_id(d) if "get_decision_submit_id" in globals() else d.get("id"),
            "order": d.get("displayOrder"),
            "assetA": ticker_a,
            "assetB": ticker_b,
            "picked": picked,
            "result": None,  # diisi nanti pas settled: "win" / "loss" / "unknown"
        })

    record = {
        "roundId": round_id,
        "submittedAt": now_wib(),
        "picks": picks,
        "settled": False,
    }
    append_history(record)
    log.debug(f"History tersimpan untuk round {round_id[:30]} ({len(picks)} picks)")


def find_settlement_result(decisions: list) -> list:
    """
    Cek field hasil settlement di tiap decision setelah round SETTLED.
    Nama field belum dikonfirmasi — coba beberapa alias umum dulu.
    """
    results = []
    for d in decisions:
        ticker_a = d.get("assetAId", "").replace("asset-", "")
        ticker_b = d.get("assetBId", "").replace("asset-", "")
        picked = (d.get("pickedAssetId") or d.get("selectedAssetId") or d.get("_botPickedAssetId") or "").replace("asset-", "")

        # Coba beberapa nama field yang mungkin dipakai API untuk hasil
        winner = None
        for key in ("winningAssetId", "winnerAssetId", "settledWinnerId", "resultAssetId"):
            if d.get(key):
                winner = d[key].replace("asset-", "")
                break

        is_correct = None
        for key in ("isCorrect", "won", "isWin", "correct"):
            if key in d and d[key] is not None:
                is_correct = bool(d[key])
                break

        result = "unknown"
        if winner:
            result = "win" if winner == picked else "loss"
        elif is_correct is not None:
            result = "win" if is_correct else "loss"

        results.append({
            "decisionId": d.get("id"),
            "order": d.get("displayOrder"),
            "assetA": ticker_a,
            "assetB": ticker_b,
            "picked": picked,
            "winner": winner,
            "result": result,
        })

    return results


def update_history_with_settlement(round_id: str, decisions: list):
    """
    Update entry history yang match round_id ini dengan hasil settlement.
    Karena pakai JSONL (append-only), caranya: baca semua, update yang cocok, tulis ulang semua.
    """
    if not HISTORY_FILE.exists():
        log.debug(f"History file belum ada, skip update settlement untuk {round_id[:30]}")
        return

    results = find_settlement_result(decisions)
    has_known_result = any(r["result"] != "unknown" for r in results)

    if not has_known_result:
        log.debug(f"Field hasil settlement belum dikenali untuk round {round_id[:30]} — cek raw decision di debug")
        log.debug(f"Raw decision sample: {json.dumps(decisions[0])[:500] if decisions else 'no decisions'}")

    lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    updated_lines = []
    found = False

    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            updated_lines.append(line)
            continue

        if record.get("roundId") == round_id and not record.get("settled"):
            record["settled"] = True
            record["settledAt"] = now_wib()
            record["picks"] = results
            found = True

        updated_lines.append(json.dumps(record, ensure_ascii=False))

    if found:
        HISTORY_FILE.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
        wins = sum(1 for r in results if r["result"] == "win")
        losses = sum(1 for r in results if r["result"] == "loss")
        unknown = sum(1 for r in results if r["result"] == "unknown")

        if wins or losses:
            log.info(f"📊 Hasil round: {wins} win, {losses} loss" + (f", {unknown} unknown" if unknown else ""))
        else:
            log.debug(f"Round {round_id[:30]} settled tapi hasil belum bisa diidentifikasi dari API")
    else:
        log.debug(f"Round {round_id[:30]} tidak ketemu di history (mungkin belum pernah submit lewat bot ini)")


def now_utc():
    return datetime.now(timezone.utc)


def now_wib():
    return (now_utc() + timedelta(hours=7)).strftime("%d %b %Y, %H:%M:%S WIB")


def sep(char="─", n=44):
    log.info(char * n)


def parse_dt(iso_str):
    if not iso_str:
        return None

    try:
        return datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
    except Exception:
        return None


def fmt_time(iso_str):
    dt = parse_dt(iso_str)

    if not dt:
        return "N/A"

    wib = dt + timedelta(hours=7)
    return wib.strftime("%H:%M WIB")


def fmt_datetime_wib(iso_str):
    """Format ISO datetime ke 'Jul 2, 2026, 10:00 AM WIB' seperti tampilan web."""
    dt = parse_dt(iso_str)
    if not dt:
        return "N/A"
    wib = dt + timedelta(hours=7)
    return wib.strftime("%-d %b %Y, %-I:%M %p WIB")


def fmt_countdown(seconds):
    if seconds is None:
        return "-"

    if seconds <= 0:
        return "sekarang"

    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60

    if h > 0:
        return f"{h}j {m:02d}m"

    if m > 0:
        return f"{m}m {sec:02d}s"

    return f"{sec}s"


def seconds_until(iso_value):
    dt = parse_dt(iso_value)

    if not dt:
        return None

    return int((dt - now_utc()).total_seconds())


def get_headers():
    # Prioritas: session file → fallback .env EDEL_COOKIE
    cookie = get_cookie_header_from_session()
    if not cookie and not MULTI_ACCOUNT_MODE:
        cfg = load_env()
        cookie = cfg.get("EDEL_COOKIE", "")

    return {
        "Cookie": cookie,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": f"{BASE_URL}/listing-calls",
        "Origin": BASE_URL,
    }


class EndpointNotFoundError(Exception):
    pass


class ServerBusyError(Exception):
    pass


def api_get(path):
    r = requests.get(f"{BASE_URL}{path}", headers=get_headers(), timeout=API_TIMEOUT)
    log.debug(f"GET {path} → HTTP {r.status_code}")
    if r.status_code == 404:
        # Kalau ada cookie tapi tetap 404, kemungkinan cookie expired/invalid —
        # TAPI jangan langsung divonis dari satu kali 404 (lihat Suspected404Error).
        has_cookie = bool(get_cookie_header_from_session() or ((not MULTI_ACCOUNT_MODE) and load_env().get("EDEL_COOKIE")))
        if has_cookie:
            raise Suspected404Error(f"HTTP 404 di {path} — cookie mungkin expired, mungkin juga server lagi glitch")
        raise EndpointNotFoundError(f"Endpoint tidak ditemukan (404): {path} — kemungkinan URL API sudah berubah")
    if r.status_code in (500, 502, 503, 504):
        raise ServerBusyError(f"HTTP {r.status_code} — server Edel lagi sibuk/down, retry nanti")
    r.raise_for_status()
    return r.json()


def api_post(path, body):
    r = requests.post(f"{BASE_URL}{path}", headers=get_headers(), json=body, timeout=API_TIMEOUT)
    log.debug(f"POST {path} → HTTP {r.status_code}")

    try:
        return r.json(), r.status_code
    except Exception:
        return {"raw": r.text[:300]}, r.status_code


def api_error_message(data) -> str:
    if isinstance(data, dict):
        err = safe_dict(data.get("error"))
        msg = err.get("message") or data.get("message") or data.get("raw") or str(data)
        return str(msg)[:220]
    return str(data)[:220]


def is_retryable_action_response(data, status_code: int) -> bool:
    if status_code in (408, 409, 425, 429, 500, 502, 503, 504):
        return True
    msg = api_error_message(data).lower()
    return any(x in msg for x in (
        "gateway time-out",
        "bad gateway",
        "timeout",
        "timed out",
        "temporarily",
        "try again",
        "server busy",
    ))


def api_post_with_retries(path, body, attempts: int, label: str):
    """POST dengan retry untuk delay/blank server Edel seperti yang kelihatan di HAR."""
    last_result, last_status = None, None

    for attempt in range(1, max(1, attempts) + 1):
        result, status_code = api_post(path, body)
        last_result, last_status = result, status_code

        if status_code in (200, 201):
            if attempt > 1:
                log.info(f"✅ {label} tembus setelah retry #{attempt}")
            return result, status_code

        if is_cookie_expired(result, status_code):
            return result, status_code

        msg = api_error_message(result)
        msg_low = msg.lower()

        # 404 / Route not found pada endpoint action berarti path salah, bukan delay.
        if status_code == 404 or "route not found" in msg_low:
            return result, status_code

        if attempt < attempts and is_retryable_action_response(result, status_code):
            log.warning(f"⏳ {label} belum tembus HTTP {status_code}: {msg} — retry {attempt}/{attempts}")
            time.sleep(ACTION_RETRY_DELAY)
            continue

        return result, status_code

    return last_result, last_status


def is_cookie_expired(response_data, status_code):
    if status_code in (401, 403):
        return True

    if isinstance(response_data, dict):
        msg = str(safe_dict(response_data.get("error")).get("message", "")).lower()

        if any(x in msg for x in ["unauthorized", "unauthenticated", "session", "expired"]):
            return True

    return False


def cookie_works(cookie_value: str) -> bool:
    """Tes apakah cookie ini beneran valid — pake /assets (endpoint ringan, tidak 404)."""
    try:
        r = requests.get(
            f"{BASE_URL}/assets",
            headers={
                "Cookie": cookie_value,
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def get_telegram_updates(offset: int = 0) -> list:
    """Ambil pesan masuk dari Telegram bot (long-poll singkat)"""
    if not TG_TOKEN:
        return []
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 3, "limit": 5},
            timeout=8,
        )
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception as e:
        log.debug(f"getUpdates error: {e}")
    return []


def extract_cookie_from_message(text: str) -> str | None:
    """
    Parse cookie dari teks user. Return cookie header string siap pakai.
    Format yang diterima:
      1. eyJ...                      → raw JWT token (value edel_session)
      2. edel_session=eyJ...         → single cookie
      3. name1=val1; name2=val2      → full cookie string (semua cookie)
      4. Cookie: name1=val1; ...     → dengan prefix Cookie:
    """
    if not text:
        return None

    text = text.strip()

    # Hapus prefix "Cookie: " kalau ada
    if text.lower().startswith("cookie:"):
        text = text[7:].strip()

    # Format raw JWT token
    if text.startswith("eyJ") and len(text) > 20 and " " not in text:
        return f"edel_session={text}"

    # Full cookie string atau single edel_session=VALUE
    if "=" in text:
        cookies = parse_cookie_string(text)
        if not cookies:
            return None
        # Wajib ada edel_session
        if any(c["name"] == "edel_session" for c in cookies):
            return "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    return None


def refresh_cookie_via_browser():
    """
    Dua jalur:
    - Single account: Chrome lokal + Telegram worker lama.
    - Multi account: worker tidak poll Telegram sendiri; manager pusat akan update sessions/<akun>.json.
    """
    log.info("🍪 Cookie expired — menunggu login ulang")

    if MULTI_ACCOUNT_MODE and not WORKER_TELEGRAM_POLL:
        if _cookie_expired_notify_allowed():
            send_telegram(
                f"🍪 *Cookie Edel expired*\n\n"
                f"Kirim cookie baru ke chat ini dengan prefix akun:\n"
                f"`@{ACCOUNT_ID} edel_session=NILAI_COOKIE`\n\n"
                f"Worker akan lanjut otomatis begitu `{SESSION_FILE.name}` valid."
            )
            _mark_cookie_expired_notified()
        else:
            log.info("🔕 Baru aja kirim notif cookie expired — skip biar gak spam Telegram (restart/testing loop)")
        waited = 0
        reminder_every = 10 * 60
        last_reminder = 0
        check_interval = 5
        while True:
            cookie = get_cookie_header_from_session()
            if cookie and cookie_works(cookie):
                log.info("✅ Cookie baru terdeteksi di session file — lanjut")
                send_telegram("✅ *Cookie baru valid* — worker lanjut otomatis.")
                _reset_cookie_expired_notify()  # cookie baru masuk — kalau nanti expired lagi, notif langsung kekirim lagi
                return True

            time.sleep(check_interval)
            waited += check_interval
            if waited - last_reminder >= reminder_every:
                last_reminder = waited
                mins = waited // 60
                log.info(f"⏳ Masih menunggu cookie ({mins} menit berlalu)")
                send_telegram(
                    f"⏳ *Masih menunggu cookie* \\({mins} menit\\)\\.\n\n"
                    f"Format: `@{ACCOUNT_ID} edel_session=NILAI_COOKIE`"
                )

    if _cookie_expired_notify_allowed():
        send_telegram(
            "🍪 *Cookie Edel expired*\n\n"
            "Chrome laptop sudah dibuka otomatis — login di sana kalau bisa.\n\n"
            "Atau, dari device mana pun (iPhone/iPad/dll):\n"
            "1. Buka runway\\.edel\\.finance di browser → login\n"
            "2. F12 → Application → Cookies → `edel_session` → copy value\n"
            "3. Kirim ke sini: `edel_session=NILAI_COOKIE`\n\n"
            "Bot otomatis lanjut begitu dapat cookie yang valid."
        )
        _mark_cookie_expired_notified()
    else:
        log.info("🔕 Baru aja kirim notif cookie expired — skip biar gak spam Telegram (restart/testing loop)")

    # Import cookie_refresher (Chrome path) — boleh gagal, Telegram path tetap jalan
    open_chrome_fn = None
    read_cookie_fn = None
    try:
        from cookie_refresher import open_chrome, read_cookie
        open_chrome_fn = open_chrome
        read_cookie_fn = read_cookie
    except ImportError as e:
        log.warning(f"⚠️  cookie_refresher tidak lengkap ({e}) — hanya pakai jalur Telegram")

    if open_chrome_fn:
        try:
            open_chrome_fn()
        except Exception as e:
            log.warning(f"⚠️  Gagal buka Chrome: {e}")

    waited = 0
    reminder_every = 10 * 60
    last_reminder = 0
    check_interval = 5
    tg_update_offset = 0

    while True:
        # ── Jalur 1: baca cookie dari Chrome laptop ──
        if read_cookie_fn:
            try:
                chrome_cookie = read_cookie_fn()
                if chrome_cookie and cookie_works(chrome_cookie):
                    cookies = parse_cookie_string(chrome_cookie)
                    save_session_file(build_session_state(cookies))
                    log.info("✅ Cookie dari Chrome laptop valid — lanjut")
                    send_telegram("✅ *Cookie diperbarui dari Chrome laptop* — bot lanjut otomatis.")
                    _reset_cookie_expired_notify()
                    return True
            except Exception as e:
                log.debug(f"read_cookie error: {e}")

        # ── Jalur 2: poll Telegram via handle_telegram_message ──
        if poll_telegram_once():
            log.info("✅ Cookie baru dari Telegram diterima saat waiting — lanjut")
            _reset_cookie_expired_notify()
            return True

        time.sleep(check_interval)
        waited += check_interval

        if waited - last_reminder >= reminder_every:
            last_reminder = waited
            mins = waited // 60
            log.info(f"⏳ Masih menunggu cookie ({mins} menit berlalu)")
            send_telegram(
                f"⏳ *Masih menunggu* \\({mins} menit\\)\\.\n\n"
                "Login di Chrome laptop, atau kirim cookie dari device lain:\n"
                "`edel_session=NILAI_COOKIE`"
            )


def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        return

    if MULTI_ACCOUNT_MODE:
        text = f"*{ACCOUNT_ID}*\n{text}"

    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram gagal: {e}")


def send_telegram_reply(chat_id: str, text: str):
    """Kirim pesan ke chat_id tertentu (untuk reply command)."""
    if not TG_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram reply gagal: {e}")


# State global untuk Telegram listener (offset polling)
_tg_offset = 0
_tg_force_vote = False  # flag untuk trigger vote dari Telegram


def handle_telegram_message(upd: dict) -> bool:
    """
    Handle pesan Telegram masuk. Return True kalau ada cookie baru yang valid.
    Side effect: set _tg_force_vote = True kalau dapat /vote command.
    """
    global _tg_offset, _tg_force_vote

    msg = upd.get("message", {})
    text = msg.get("text", "").strip()
    chat_id = str(msg.get("chat", {}).get("id", ""))

    if not text:
        return False

    # Keamanan: hanya terima dari chat_id terdaftar
    if TG_CHAT_ID and chat_id != str(TG_CHAT_ID):
        log.debug(f"Pesan diblokir dari chat_id asing: {chat_id}")
        return False

    # /start atau /help
    if text in ("/start", "/help", "help"):
        send_telegram_reply(chat_id,
            "👋 *Edel Runway Bot v5\\.5*\n\n"
            "Kirim cookie baru langsung ke chat ini untuk update session\\.\n\n"
            "📌 *Command:*\n"
            "• `/status` \\— cek status session & round\n"
            "• `/vote` \\— force vote manual sekarang\n\n"
            "🍪 *Update cookie:*\n"
            "Paste salah satu format ini:\n"
            "• `edel_session=eyJ\\.\\.\\.`\n"
            "• `eyJ\\.\\.\\.` \\(JWT langsung\\)\n"
            "• Full cookie string \\(`name1=val1; name2=val2; \\.\\.\\.`\\)"
        )
        return False

    # /status
    if text == "/status":
        edel_val = get_edel_session_value()
        has_session = bool(get_cookie_header_from_session())

        if not has_session:
            send_telegram_reply(chat_id, "⚠️ *Belum ada session\\.* Kirim cookie dulu\\!")
            return False

        exp_ts = get_token_expiry_ts(edel_val or "")
        if exp_ts:
            remaining_s = exp_ts - now_utc().timestamp()
            if remaining_s < 0:
                send_telegram_reply(chat_id, "🔴 *Session EXPIRED*\\. Kirim cookie baru segera\\!")
            else:
                h = int(remaining_s // 3600)
                m = int((remaining_s % 3600) // 60)
                expire_wib = (
                    datetime.fromtimestamp(exp_ts, tz=timezone.utc) + timedelta(hours=7)
                ).strftime("%d %b %H:%M WIB")
                send_telegram_reply(chat_id,
                    f"✅ *Session aktif*\n\n"
                    f"⏰ Sisa: *{h}j {m}m*\n"
                    f"🔑 Expired: {expire_wib}"
                )
        else:
            send_telegram_reply(chat_id, "✅ *Session ada* \\(expiry tidak bisa dibaca dari token\\)\\."),
        return False

    # /vote
    if text == "/vote":
        _tg_force_vote = True
        send_telegram_reply(chat_id, "🗳️ *Force vote akan dilakukan di tick berikutnya\\.\\.\\.*")
        return False

    # Coba parse sebagai cookie
    cookie_candidate = extract_cookie_from_message(text)
    if cookie_candidate:
        log.info("📨 Cookie diterima via Telegram — memvalidasi...")
        send_telegram_reply(chat_id, "🔄 *Sedang memverifikasi cookie\\.\\.\\.* ")
        if cookie_works(cookie_candidate):
            cookies = parse_cookie_string(cookie_candidate)
            save_session_file(build_session_state(cookies))
            log.info("✅ Cookie dari Telegram valid & disimpan ke session file")
            edel_val = get_edel_session_value()
            exp_ts = get_token_expiry_ts(edel_val or "")
            if exp_ts:
                expire_wib = (
                    datetime.fromtimestamp(exp_ts, tz=timezone.utc) + timedelta(hours=7)
                ).strftime("%d %b %H:%M WIB")
                remaining_s = exp_ts - now_utc().timestamp()
                h = int(remaining_s // 3600)
                m = int((remaining_s % 3600) // 60)
                send_telegram_reply(chat_id,
                    f"✅ *Cookie valid & berhasil dipasang\\!*\n\n"
                    f"⏰ Sisa: *{h}j {m}m* \\(expired {expire_wib}\\)\n"
                    "Bot melanjutkan voting otomatis\\."
                )
            else:
                send_telegram_reply(chat_id, "✅ *Cookie valid & berhasil dipasang\\!* Bot lanjut otomatis\\.")
            return True  # sinyal ke caller bahwa cookie baru sudah tersimpan
        else:
            log.warning("❌ Cookie dari Telegram tidak valid (API menolak)")
            send_telegram_reply(chat_id,
                "❌ *Cookie tidak valid* \\(API menolak\\)\\.\n\n"
                "Pastikan kamu sudah *login dulu*, lalu copy cookie baru\\.\n"
                "Endpoint cek: `/assets`"
            )
        return False

    # Pesan lain — abaikan atau balas kalau ada "/"
    if text.startswith("/"):
        send_telegram_reply(chat_id, "❓ Perintah tidak dikenal\\. Kirim `/help` untuk bantuan\\.")
    return False


def poll_telegram_once() -> bool:
    """
    Poll Telegram sekali dan proses semua update masuk.
    Return True kalau ada cookie baru yang valid diterima.
    """
    global _tg_offset
    if not WORKER_TELEGRAM_POLL:
        return False
    if not TG_TOKEN:
        return False

    updates = get_telegram_updates(offset=_tg_offset)
    got_new_cookie = False
    for upd in updates:
        _tg_offset = upd["update_id"] + 1
        if handle_telegram_message(upd):
            got_new_cookie = True
    return got_new_cookie


def get_current_round():
    try:
        return api_get("/listing-round")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (401, 403):
            raise CookieExpiredError(f"HTTP {e.response.status_code}")
        raise


def units_to_edelx(amount_obj):
    """
    Konversi field {"units": "682400000000", "decimals": 10} jadi float EDELx.
    Contoh: units=682400000000, decimals=10 → 68.24 EDELx
    """
    if not isinstance(amount_obj, dict):
        return None

    units = amount_obj.get("units")
    decimals = amount_obj.get("decimals")

    if units is None or decimals is None:
        return None

    try:
        return int(units) / (10 ** int(decimals))
    except (TypeError, ValueError):
        return None


def fmt_edelx(value):
    if value is None:
        return "?"
    try:
        return f"{float(value):.2f} EDELx"
    except (TypeError, ValueError):
        return str(value)


def get_round_stake_summary(data):
    """
    Ambil EDELx yang di-stake/locked untuk round ini.
    Struktur baru: data.round.stakeAmount atau data.preview.stakeAmount
    """
    data = safe_dict(data)
    round_data = safe_dict(data.get("round"))
    preview = safe_dict(data.get("preview"))

    # Coba dari round dulu, fallback ke preview
    source = round_data if round_data else preview

    stake_amount = source.get("stakeAmount")
    total_staked = units_to_edelx(stake_amount)

    decisions = source.get("decisions")
    if not isinstance(decisions, list):
        decisions = source.get("options")
    decisions = decisions if isinstance(decisions, list) else []

    per_call = []
    for d in decisions:
        amt = units_to_edelx(d.get("stakeAmount"))
        per_call.append({
            "order": d.get("displayOrder", "?"),
            "amount": amt,
        })

    if total_staked is None and not per_call:
        return None

    return {
        "round_id": source.get("id") or source.get("roundId"),
        "total_staked": total_staked,
        "per_call": per_call,
        "decision_count": source.get("decisionCount", len(decisions)),
    }


def round_stake_changed(state, stake_summary):
    """Cek apakah stake amount untuk round ini berubah dari snapshot terakhir"""
    if not stake_summary:
        return False

    key = f"stake_snapshot_{stake_summary.get('round_id')}"
    snapshot = json.dumps(stake_summary, sort_keys=True)
    changed = state.get(key) != snapshot
    state[key] = snapshot
    return changed


def start_round():
    # HAR web terbaru: start/prepare round = POST /listing-round, body {}
    return api_post_with_retries("/listing-round", {}, START_RETRY_ATTEMPTS, "START")


def pick_asset(asset_a_id, asset_b_id):
    ticker_a = asset_a_id.replace("asset-", "").upper()
    ticker_b = asset_b_id.replace("asset-", "").upper()

    score_a = LARGE_CAPS.get(ticker_a, 0)
    score_b = LARGE_CAPS.get(ticker_b, 0)

    return asset_a_id if score_a >= score_b else asset_b_id


def get_decision_submit_id(d: dict) -> str | None:
    return d.get("listingDecisionId") or d.get("decisionId") or d.get("roundDecisionId") or d.get("id")


def submit_picks(preview_id, decisions):
    """
    HAR web terbaru:
      POST /listing-round/submit
      {"previewId":"decision-preview-...", "picks":[{"listingDecisionId":"...", "assetId":"asset-..."}]}
    """
    picks = []
    matchups = []

    for d in decisions:
        asset_a = d.get("assetAId")
        asset_b = d.get("assetBId")
        decision_id = get_decision_submit_id(d)
        if not asset_a or not asset_b or not decision_id:
            log.debug(f"Skip decision tidak lengkap: {str(d)[:250]}")
            continue

        asset_id = pick_asset(asset_a, asset_b)
        d["_botPickedAssetId"] = asset_id

        ticker_a = asset_a.replace("asset-", "").upper()
        ticker_b = asset_b.replace("asset-", "").upper()
        chosen = asset_id.replace("asset-", "").upper()

        picks.append({
            "listingDecisionId": decision_id,
            "assetId": asset_id,
        })

        matchups.append({
            "order": d.get("displayOrder", "?"),
            "a": ticker_a,
            "b": ticker_b,
            "chosen": chosen,
            "score_a": LARGE_CAPS.get(ticker_a, 0),
            "score_b": LARGE_CAPS.get(ticker_b, 0),
        })

    if not picks:
        return {
            "result": {"error": {"message": "Tidak ada decision valid untuk submit"}},
            "status": 0,
            "matchups": matchups,
            "count": 0,
        }

    result, status_code = api_post_with_retries(
        "/listing-round/submit",
        {"previewId": preview_id, "picks": picks},
        SUBMIT_RETRY_ATTEMPTS,
        "SUBMIT",
    )

    return {
        "result": result,
        "status": status_code,
        "matchups": matchups,
        "count": len(picks),
    }


def collect_text(obj):
    parts = []

    if isinstance(obj, dict):
        for v in obj.values():
            parts.extend(collect_text(v))
    elif isinstance(obj, list):
        for v in obj:
            parts.extend(collect_text(v))
    elif obj is not None:
        parts.append(str(obj))

    return parts


def get_timing(data):
    data = safe_dict(data)
    # Prioritas: round.timing (lebih spesifik), fallback ke currentWindow.timing
    round_data = safe_dict(data.get("round"))
    round_timing = safe_dict(round_data.get("timing"))
    if round_timing:
        return round_timing
    current_window = safe_dict(data.get("currentWindow"))
    return safe_dict(current_window.get("timing"))


def get_round_id(data):
    data = safe_dict(data)
    round_data = safe_dict(data.get("round"))
    preview = safe_dict(data.get("preview"))
    current_window = safe_dict(data.get("currentWindow"))

    return (
        round_data.get("id")
        or round_data.get("roundId")
        or preview.get("id")
        or preview.get("previewId")
        or data.get("roundId")
        or current_window.get("roundWindowId")
        or current_window.get("id")
        or "active-window"
    )


def get_preview_id(data):
    """
    API web submit butuh preview.id = decision-preview-...
    round.id kadang berbentuk listing-round:decision-preview-...
    """
    data = safe_dict(data)
    preview = safe_dict(data.get("preview"))
    round_data = safe_dict(data.get("round"))

    preview_id = (
        preview.get("id")
        or preview.get("previewId")
        or data.get("previewId")
        or round_data.get("previewId")
    )

    if not preview_id:
        round_id = get_round_id(data)
        if isinstance(round_id, str) and round_id.startswith("listing-round:"):
            preview_id = round_id.replace("listing-round:", "", 1)
        else:
            preview_id = round_id

    return preview_id


def get_decisions(data):
    data = safe_dict(data)
    round_data = safe_dict(data.get("round"))

    # Struktur API baru dari HAR:
    # - sebelum submit: preview.options
    # - sesudah submit: round.decisions
    decisions = round_data.get("decisions")
    if not isinstance(decisions, list):
        preview = safe_dict(data.get("preview"))
        decisions = preview.get("decisions")
        if not isinstance(decisions, list):
            decisions = preview.get("options")
    return decisions if isinstance(decisions, list) else []


def get_start_action(data):
    data = safe_dict(data)
    actions = safe_dict(data.get("actions"))
    # Nama action berubah: startRound → prepareRound
    return safe_dict(actions.get("prepareRound") or actions.get("startRound"))


def get_submit_action(data):
    data = safe_dict(data)
    actions = safe_dict(data.get("actions"))
    round_data = safe_dict(data.get("round"))
    round_actions = safe_dict(round_data.get("actions"))
    # Nama action berubah: submitPicks → submitPreview
    return safe_dict(
        actions.get("submitPreview")
        or actions.get("submitPicks")
        or round_actions.get("submitPicks")
        or round_actions.get("submitPreview")
    )


def get_status_from_api(data):
    data = safe_dict(data)
    round_data = safe_dict(data.get("round"))
    actions = safe_dict(data.get("actions"))

    # Kalau ada round object dengan status → pakai langsung
    round_status = str(round_data.get("status", "")).upper()
    if round_status:
        return round_status

    # Cek dari actions — ini cara baru deteksi status
    prepare = safe_dict(actions.get("prepareRound") or actions.get("startRound"))
    submit = safe_dict(actions.get("submitPreview") or actions.get("submitPicks"))

    prepare_reason = str(prepare.get("reason", "")).upper()
    submit_reason = str(submit.get("reason", "")).upper()

    if prepare_reason == "PREVIOUS_ROUND_SETTLEMENT_PENDING":
        # Ini sering muncul saat server masih delay settle. Jangan stop bot, tunggu dan retry.
        return "SETTLEMENT_PENDING"

    if submit_reason == "NO_ACTIVE_DECISION_PREVIEW":
        # Kalau prepare enabled → bisa start round baru
        if prepare.get("enabled"):
            return "NO_ROUND"
        return "CREATED"

    if submit.get("enabled"):
        return "OPEN"

    if prepare.get("enabled"):
        return "NO_ROUND"

    # Fallback: cek currentWindow
    current_window = safe_dict(data.get("currentWindow"))
    if current_window:
        return "CREATED"

    return "NO_ROUND"

    return "NO_ROUND"


def get_picked_list(decisions):
    picked_list = []

    for d in decisions:
        picked = (
            d.get("pickedAssetId")
            or d.get("pickedAssetID")
            or d.get("selectedAssetId")
            or d.get("selectedAssetID")
            or d.get("assetId")
            or "?"
        )

        picked = str(picked).replace("asset-", "").upper()

        if picked and picked != "?":
            picked_list.append(picked)

    return picked_list


def log_round_submitted(decisions, stake_summary=None):
    picked_list = get_picked_list(decisions)

    sep()
    if stake_summary and stake_summary.get("total_staked") is not None:
        log.info(f"✅ Submitted — {fmt_edelx(stake_summary['total_staked'])} staked")
    else:
        log.info("✅ Submitted")

    if picked_list:
        log.info(f"🎯 {' '.join(picked_list)}")
    sep()


def status_text(status):
    labels = {
        "CREATED": "🟡 Calls dibuat, menunggu alokasi",
        "OPEN": "🟢 Calls open — bisa pilih",
        "LOCKED": "🔒 Locked, menunggu settlement",
        "LOCK_PENDING": "⏳ Allocation Pending — EDELx lock in progress",
        "SUBMITTED": "✅ Submitted",
        "DEMAND_INDEX_PENDING": "🔄 Demand index diproses",
        "SETTLEMENT_PENDING": "⏳ Settlement server pending — tunggu retry",
        "SETTLED": "🏁 Final",
        "NO_ROUND": "⚪ Belum ada round",
    }
    return labels.get(status, f"❓ {status}")


def log_status_once(state, round_id, status):
    key = f"status_{round_id}"
    label = status_text(status)

    if state.get(key) == label:
        return

    log.info(label)
    state[key] = label


def log_pipeline(status, timing):
    """Satu baris ringkas: status + window timing (kalau relevan)"""
    open_at = timing.get("selectionOpensAt")
    close_at = timing.get("selectionClosesAt")

    if status == "OPEN" and open_at and close_at:
        log.info(f"{status_text(status)} — tutup {fmt_time(close_at)}")
    else:
        log.info(status_text(status))


def settlement_bucket(seconds_left):
    if seconds_left is None:
        return "unknown"

    minutes_left = max(0, int(seconds_left // 60))

    if minutes_left > 45:
        return "45+"
    if minutes_left > 30:
        return "30+"
    if minutes_left > 15:
        return "15+"
    if minutes_left > 5:
        return "5+"

    return "final"


def should_log_settlement(state, round_id, settle_seconds_left):
    bucket = settlement_bucket(settle_seconds_left)
    key = f"settlement_bucket_{round_id}"

    if state.get(key) == bucket:
        return False

    state[key] = bucket
    return True


def log_settlement_throttled(state, round_id, data, status="LOCKED"):
    timing = get_timing(data)

    selection_closes_at = timing.get("selectionClosesAt")
    settlement_eligible_at = timing.get("settlementEligibleAt")
    estimated_settle_at = timing.get("estimatedNextSettlementAttemptBy")

    close_left = seconds_until(selection_closes_at)
    settle_left = seconds_until(estimated_settle_at)

    if not should_log_settlement(state, round_id, settle_left):
        return

    # Baris 1: Settlement eligible (seperti web)
    if settlement_eligible_at:
        log.info(f"   Settlement eligible: {fmt_datetime_wib(settlement_eligible_at)}")

    # Baris 2: Status (persis seperti web)
    status_labels = {
        "SUBMITTED": "Selections Submitted",
        "DEMAND_INDEX_PENDING": "Demand Index Pending",
        "LOCKED": "Locked",
        "LOCK_PENDING": "Allocation Pending / EDELx lock in progress",
        "SETTLEMENT_PENDING": "Previous Settlement Pending",
    }
    status_label = status_labels.get(status, status)
    log.info(f"   Status: {status_label}")

    # Baris 3: Countdown window + estimasi settle
    parts = []
    if close_left is not None and close_left > 0:
        parts.append(f"window tutup {fmt_countdown(close_left)}")
    if settle_left is not None and settle_left > 0:
        parts.append(f"estimasi settle {fmt_countdown(settle_left)}")
    if parts:
        log.info(f"   ⏱  {' | '.join(parts)}")


def log_submit_success(matchups, stake_summary=None):
    picked = " ".join(m["chosen"] for m in matchups)

    sep()
    if stake_summary and stake_summary.get("total_staked") is not None:
        log.info(f"✅ Submitted — {fmt_edelx(stake_summary['total_staked'])} staked")
    else:
        log.info("✅ Submitted")
    log.info(f"🎯 {picked}")
    sep()


def one_tick(state):
    last_round_id = state.get("last_round_id", "")
    last_status = state.get("last_status", "")

    try:
        data = get_current_round()

    except CookieExpiredError as e:
        log.warning(f"🍪 Cookie expired: {e}")
        state["cookie_expired"] = True
        return state

    except Suspected404Error as e:
        # 404 doang belum tentu cookie expired — kasih toleransi MAX_API_ERRORS kali
        # (sama seperti error generik lain) sebelum beneran minta cookie baru.
        state["api_errors"] = state.get("api_errors", 0) + 1
        log.warning(f"⚠️ {e} (percobaan ke-{state['api_errors']}/{MAX_API_ERRORS})")

        if state["api_errors"] >= MAX_API_ERRORS:
            log.warning(f"⚠️ 404 konsisten {MAX_API_ERRORS}x berturut — kemungkinan besar cookie memang expired")
            state["cookie_expired"] = True
            state["api_errors"] = 0
        else:
            state["last_tick"] = now_wib()

        return state

    except ServerBusyError as e:
        # HAR acc4: browser juga beberapa kali dapat 504 lalu sukses lagi.
        # Jangan spam warning dan jangan anggap cookie/api berubah kalau state terakhir sudah jelas.
        now_ts = time.time()
        last_known = state.get("last_status") or ""
        last_log_ts = float(state.get("server_busy_last_log_ts") or 0)

        if now_ts - last_log_ts >= SERVER_BUSY_LOG_COOLDOWN:
            if last_known:
                log.info(
                    f"🌐 Web/API lagi lambat: {e} — state terakhir {last_known}, "
                    f"bot ikut web: tunggu dan retry otomatis"
                )
            else:
                log.warning(f"⏳ Server Edel sibuk: {e} — skip tick, retry {POLL_INTERVAL}s lagi")
            state["server_busy_last_log_ts"] = now_ts

        state["last_tick"] = now_wib()
        return state

    except EndpointNotFoundError as e:
        log.error(f"🚫 {e}")
        log.error("❌ Bot dihentikan — endpoint API sudah berubah, perlu update BASE_URL atau path endpoint di kode.")
        log.error("   Cara fix: buka runway.edel.finance di browser, buka DevTools → Network, login/refresh halaman,")
        log.error("   lalu cari request ke /listing-rounds atau /rounds dan lihat URL yang benar.")
        state["endpoint_not_found"] = True
        return state

    except Exception as e:
        log.error(f"❌ API error: {e}")
        state["api_errors"] = state.get("api_errors", 0) + 1

        if state["api_errors"] >= MAX_API_ERRORS:
            log.warning(f"⚠️ {MAX_API_ERRORS}x error berturut — coba refresh cookie")
            state["cookie_expired"] = True
            state["api_errors"] = 0

        return state

    state["api_errors"] = 0

    if not data:
        log.warning("⚠️ Response kosong")
        state["cookie_expired"] = True
        return state

    if is_cookie_expired(data, 200):
        log.warning("🍪 Cookie expired dari response")
        state["cookie_expired"] = True
        return state

    status = get_status_from_api(data)
    round_id = get_round_id(data)
    preview_id = get_preview_id(data)
    timing = get_timing(data)
    decisions = get_decisions(data)

    # Simpan jumlah listing call / vote round ini ke state supaya dashboard UI (manager)
    # bisa nampilin kolom "Votes" (misal "3/5"). Cuma di-update kalau ada data beneran,
    # biar gak ke-reset ke 0 pas lagi transisi antar round / server ngasih data kosong.
    if decisions:
        state["round_votes_total"] = len(decisions)
        state["round_votes_picked"] = len(get_picked_list(decisions))
        state["round_votes_round_id"] = round_id

    # Kalau server bilang settlement sebelumnya masih pending, jangan matikan bot.
    # Kondisi ini dari HAR/real web sering sementara karena backend delay.
    if status in ("SETTLEMENT_BUG", "SETTLEMENT_PENDING"):
        actions = safe_dict(data.get("actions"))
        prepare = safe_dict(actions.get("prepareRound") or actions.get("startRound"))
        blocking_id = prepare.get("blockingRoundId", "unknown")
        key = f"settlement_pending_{blocking_id}"
        if state.get(key) != True:
            log.warning("⏳ Settlement server masih pending — bot tetap jalan dan retry tick berikutnya")
            log.warning(f"   Blocking round: {blocking_id}")
            send_telegram(
                "⏳ *Settlement server pending*\n\n"
                "Bot tidak dimatikan, akan retry otomatis di tick berikutnya\\."
            )
            state[key] = True
        state["settlement_bug"] = False
        state["last_status"] = "SETTLEMENT_PENDING"
        state["last_tick"] = now_wib()
        state["cookie_expired"] = False
        return state

    is_new_round = round_id and round_id != last_round_id
    is_status_changed = status != last_status

    if not state.get("runtime_first_check_done"):
        stake_summary = get_round_stake_summary(data)
        if stake_summary:
            round_stake_changed(state, stake_summary)  # simpan snapshot awal

        if status == "SUBMITTED":
            log_round_submitted(decisions, stake_summary)
            state["runtime_first_check_done"] = True
            state["last_round_id"] = round_id
            state["last_status"] = status
            state["last_tick"] = now_wib()
            state["cookie_expired"] = False
            return state

        elif status in (PASSIVE_ROUND_STATUSES - {"SUBMITTED"}):
            # Paksa tampil sekali di awal, walau throttle state bilang sudah pernah di-log
            # sebelum restart — restart adalah sinyal user mau lihat kondisi sekarang.
            timing = get_timing(data)
            settle_left = seconds_until(timing.get("estimatedNextSettlementAttemptBy"))
            close_left = seconds_until(timing.get("selectionClosesAt"))
            parts = []
            if settle_left is not None:
                parts.append(f"settle {fmt_countdown(settle_left)}")
            if close_left is not None:
                parts.append(f"window tutup {fmt_countdown(close_left)}")
            log.info(f"{status_text(status)}" + (f" — {' | '.join(parts)}" if parts else ""))
            should_log_settlement(state, round_id, settle_left)
            state["runtime_first_check_done"] = True
            state["last_round_id"] = round_id
            state["last_status"] = status
            state["last_tick"] = now_wib()
            state["cookie_expired"] = False
            return state

        else:
            # Status OPEN / CREATED / lainnya: log sekali, lalu LANJUT ke logic
            # start/submit di bawah — jangan return di sini supaya restart saat
            # window open langsung coba submit, tanpa nunggu tick berikutnya.
            log.info(status_text(status))
            state[f"status_{round_id}"] = status_text(status)
            state["runtime_first_check_done"] = True
            state["last_tick"] = now_wib()
            # Set last_round_id/last_status SEKARANG supaya blok is_new_round /
            # is_status_changed di bawah tidak nge-print status yang sama lagi.
            state["last_round_id"] = round_id
            state["last_status"] = status
            last_round_id = round_id
            last_status = status
            is_new_round = False
            is_status_changed = False

    if status in PASSIVE_ROUND_STATUSES:
        if status == "SUBMITTED" and state.get(f"submitted_{round_id}") != True:
            # Hanya log kalau submit dilakukan di luar bot (misalnya manual via web)
            # Kalau bot yang submit, state[submitted_X] sudah di-set di blok submit di atas
            stake_summary = get_round_stake_summary(data)
            log_round_submitted(decisions, stake_summary)
            state[f"submitted_{round_id}"] = True

            if not state.get(f"history_recorded_{round_id}"):
                record_submitted_picks(round_id, decisions)
                state[f"history_recorded_{round_id}"] = True

        log_settlement_throttled(state, round_id, data, status=status)

        state["last_round_id"] = round_id
        state["last_status"] = status
        state["last_tick"] = now_wib()
        state["cookie_expired"] = False
        return state

    if is_new_round:
        sep()
        log.info("🆕 Round baru")
        log_pipeline(status, timing)

    elif is_status_changed:
        sep()
        log_pipeline(status, timing)

    start_action = get_start_action(data)

    if start_action.get("enabled"):
        log.info("▶️ STARTING")

        try:
            result, status_code = start_round()

            if status_code in (200, 201):
                log.info("✅ STARTED")

                # Pakai response POST /listing-round langsung karena HAR menunjukkan GET berikutnya
                # kadang masih 502/504 saat web blank/delay.
                if isinstance(result, dict) and result:
                    data = result
                else:
                    time.sleep(2)
                    data = get_current_round()

                status = get_status_from_api(data)
                round_id = get_round_id(data)
                preview_id = get_preview_id(data)
                timing = get_timing(data)
                decisions = get_decisions(data)

                if status in PASSIVE_ROUND_STATUSES:
                    log_settlement_throttled(state, round_id, data, status=status)
                else:
                    log_status_once(state, round_id, status)

            else:
                err = result

                if isinstance(result, dict):
                    err = safe_dict(result.get("error")).get("message", str(result)[:200])

                log.warning(f"❌ START FAILED: {err}")

                if is_cookie_expired(result, status_code):
                    state["cookie_expired"] = True
                    return state

        except Exception as e:
            log.error(f"❌ START ERROR: {e}")
            log.debug(traceback.format_exc())

    elif start_action.get("reason"):
        # Log alasan kenapa belum bisa start, sekali per reason (anti-spam)
        reason = start_action["reason"]
        key = f"start_blocked_reason_{round_id}"
        if state.get(key) != reason:
            log.info(f"⏳ Belum bisa start round — alasan: {reason}")
            state[key] = reason

    submit_action = get_submit_action(data)
    decisions = get_decisions(data)

    if submit_action.get("enabled") and round_id and decisions:
        try:
            res_data = submit_picks(preview_id or round_id, decisions)
            status_code = res_data["status"]

            if status_code in (200, 201):
                state[f"submitted_{round_id}"] = True
                status = "SUBMITTED"

                record_submitted_picks(round_id, decisions)
                state[f"history_recorded_{round_id}"] = True

                # Ambil EDELx yang di-stake untuk round ini.
                # Pakai response submit dulu karena GET setelah submit juga sering 504 di HAR.
                stake_summary = None
                try:
                    fresh_data = res_data.get("result") if isinstance(res_data.get("result"), dict) else None
                    if not fresh_data:
                        fresh_data = get_current_round()
                    stake_summary = get_round_stake_summary(fresh_data)
                    round_stake_changed(state, stake_summary)
                except CookieExpiredError:
                    pass
                except Exception as e:
                    log.debug(f"Gagal cek stake setelah submit: {e}")

                log_submit_success(res_data["matchups"], stake_summary)

                tg_lines = ["✅ *EDEL RUNWAY — SUBMITTED*"]
                if stake_summary and stake_summary.get("total_staked") is not None:
                    tg_lines[0] += f" — {fmt_edelx(stake_summary['total_staked'])}"
                tg_lines.append("")
                tg_lines.append("🎯 " + " ".join(m["chosen"] for m in res_data["matchups"]))
                tg_lines += ["", f"🕐 {now_wib()}"]
                send_telegram("\n".join(tg_lines))

                log_settlement_throttled(state, round_id, data, status="SUBMITTED")

            else:
                err = res_data["result"]

                if isinstance(err, dict):
                    err = safe_dict(err.get("error")).get("message", str(err)[:200])

                log.warning(f"❌ SUBMIT FAILED HTTP {status_code}: {str(err)[:150]}")

                if is_cookie_expired(res_data["result"], status_code):
                    state["cookie_expired"] = True
                    return state

        except Exception as e:
            log.error(f"❌ SUBMIT ERROR: {e}")
            log.debug(traceback.format_exc())

    elif status == "SETTLED":
        if is_status_changed:
            stake_summary = get_round_stake_summary(data)
            if stake_summary and stake_summary.get("total_staked") is not None:
                log.info(f"🏁 Final — {fmt_edelx(stake_summary['total_staked'])} settled")
            else:
                log.info("🏁 Final")

            if round_id and decisions:
                update_history_with_settlement(round_id, decisions)

    elif not submit_action.get("enabled"):
        # Submit belum bisa dilakukan — log alasan sekali per reason supaya tidak spam,
        # tapi tetap kasih visibility (sebelumnya ini diam total tanpa jejak apapun).
        reason = submit_action.get("reason", "UNKNOWN")
        key = f"submit_blocked_reason_{round_id}"

        if state.get(key) != reason:
            log.info(f"⏳ Belum bisa submit — alasan: {reason}")
            state[key] = reason

    state["last_round_id"] = round_id
    state["last_status"] = status
    state["last_tick"] = now_wib()
    state["cookie_expired"] = False
    state["last_valid_ts"] = now_utc().timestamp()  # catat tiap tick sukses

    return state


def decode_session_token(cookie_value: str) -> dict | None:
    """
    Decode token edel_session (format: base64url(payload).signature).
    Bukan JWT standar 3-bagian — cuma 2 bagian, part pertama langsung
    payload JSON berisi {"v":1,"p":{...},"e":<expiry_ms>}.
    """
    if not cookie_value:
        return None

    raw = cookie_value
    if raw.startswith("edel_session="):
        raw = raw[len("edel_session="):]

    part0 = raw.split(".")[0]

    try:
        padded = part0 + "=" * (-len(part0) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        return json.loads(decoded)
    except Exception as e:
        log.debug(f"Gagal decode session token: {e}")
        return None


def get_token_expiry_ts(cookie_value: str) -> float | None:
    """Ambil expiry timestamp (detik, UTC) dari token. None kalau gagal parse."""
    data = decode_session_token(cookie_value)
    if not data:
        return None

    exp_ms = data.get("e")
    if exp_ms is None:
        return None

    try:
        return float(exp_ms) / 1000
    except (TypeError, ValueError):
        return None


def check_cookie_expiry_warning(state: dict):
    """
    Baca expiry SEBENARNYA dari token (klaim 'e', bukan estimasi/umur file).
    Tier warning:
      > 2 jam   : aman, tidak ada warning
      < 2 jam   : warning biasa (sekali)
      < 30 menit: warning mendesak, diulang tiap 5 menit
    """
    # Baca edel_session value dari session file atau .env
    cookie = get_edel_session_value() or ""

    exp_ts = get_token_expiry_ts(cookie)
    if exp_ts is None:
        return  # token tidak bisa di-decode, skip warning (handler 401 tetap jadi fallback)

    remaining_s = exp_ts - now_utc().timestamp()

    if remaining_s < 0:
        return  # sudah expired, biarkan handler normal yang tangani

    expire_wib = (
        datetime.fromtimestamp(exp_ts, tz=timezone.utc) + timedelta(hours=7)
    ).strftime("%H:%M WIB")

    URGENT_S = 30 * 60
    NORMAL_S = 2 * 3600
    URGENT_REPEAT_S = 5 * 60

    if remaining_s <= URGENT_S:
        last_urgent = state.get("urgent_warn_at_s")
        elapsed_since_warn = (
            (URGENT_S - remaining_s) if last_urgent is None
            else (now_utc().timestamp() - last_urgent)
        )

        if last_urgent is None or (now_utc().timestamp() - last_urgent) >= URGENT_REPEAT_S:
            mins_left = max(0, int(remaining_s / 60))
            log.warning(f"🔴 Cookie expired ~{mins_left} menit lagi ({expire_wib}) — MENDESAK")
            send_telegram(
                f"🔴 *MENDESAK: Cookie expired {mins_left} menit lagi* \\({expire_wib}\\)\n\n"
                "Login sekarang juga supaya bot tidak berhenti voting\\."
            )
            state["urgent_warn_at_s"] = now_utc().timestamp()
        state["normal_warned"] = True  # supaya tidak dobel sama tier normal

    elif remaining_s <= NORMAL_S:
        if not state.get("normal_warned"):
            mins_left = int(remaining_s / 60)
            log.info(f"⚠️  Cookie expired ~{mins_left} menit lagi ({expire_wib})")
            send_telegram(
                f"⚠️ *Cookie akan expired dalam ~{mins_left} menit* \\({expire_wib}\\)\\.\n\n"
                "Siapkan login sebelum bot berhenti voting\\."
            )
            state["normal_warned"] = True

    else:
        # Masih aman (>2 jam) — reset semua flag supaya warning muncul lagi nanti
        state.pop("normal_warned", None)
        state.pop("urgent_warn_at_s", None)


def main(stop_event=None):
    log.info("")
    log.info(f"── Runway Worker v5.7 — restarted, auto-sync {POLL_INTERVAL}s ──")

    # Cek session: prioritas session file, fallback ke .env EDEL_COOKIE
    has_session = bool(get_cookie_header_from_session())
    has_env_cookie = False if MULTI_ACCOUNT_MODE else bool(load_env().get("EDEL_COOKIE"))

    if not has_session and not has_env_cookie:
        if MULTI_ACCOUNT_MODE:
            log.warning("⚠️ Belum ada session. Menunggu cookie via Telegram manager.")
            log.warning(f"   Format Telegram: @{ACCOUNT_ID} edel_session=eyJ...")
            refresh_cookie_via_browser()
            has_session = bool(get_cookie_header_from_session())
            has_env_cookie = False if MULTI_ACCOUNT_MODE else bool(load_env().get("EDEL_COOKIE"))
        else:
            log.error("❌ Belum ada session. Jalankan dengan cookie dulu:")
            log.error("   1. Set EDEL_COOKIE=edel_session=eyJ... di .env")
            log.error("   2. Atau kirim cookie via Telegram ke bot")
            log.error("   Format: edel_session=eyJ... atau full cookie string")
            sys.exit(1)

    if has_session:
        log.info(f"✅ Session dimuat dari {SESSION_FILE}")
    else:
        # Migrasi: ada di .env, pindahkan ke session file
        cookie_str = load_env().get("EDEL_COOKIE", "")
        cookies = parse_cookie_string(cookie_str)
        if cookies:
            save_session_file(build_session_state(cookies))
            log.info("✅ EDEL_COOKIE dari .env dimigrasikan ke session file")

    state = load_state()
    state["runtime_first_check_done"] = False
    state["endpoint_not_found"] = False  # reset flag lama tiap restart
    state["settlement_bug"] = False      # reset tiap restart, cek ulang kondisi server

    while True:
        try:
            if stop_event is not None and stop_event.is_set():
                log.info("🔄 restart per-akun diminta — worker berhenti bersih")
                break

            global _tg_force_vote

            # Poll Telegram tiap tick — terima cookie baru atau command
            tg_new_cookie = poll_telegram_once()
            if tg_new_cookie:
                # Cookie baru dari Telegram sudah disimpan — reset error state
                state["cookie_expired"] = False
                state["api_errors"] = 0

            # Force vote dari Telegram /vote command
            if _tg_force_vote:
                _tg_force_vote = False
                log.info("🗳️ Force vote dari Telegram — jalankan tick sekarang")
                # Lanjut ke one_tick di bawah

            state = one_tick(state)
            save_state(state)

            if not state.get("cookie_expired"):
                check_cookie_expiry_warning(state)

            if state.get("endpoint_not_found"):
                log.error("🛑 Bot dihentikan karena endpoint 404. Fix dulu URL endpoint-nya.")
                break

            if state.get("settlement_bug"):
                log.warning("⏳ Settlement masih pending — bot tetap jalan, retry otomatis.")
                state["settlement_bug"] = False

            if state.get("cookie_expired"):
                refresh_cookie_via_browser()  # blocks sampai cookie valid ditemukan
                state["cookie_expired"] = False
                continue

            if stop_event is not None:
                # wait() balik True kalau stop_event di-set dari luar (restart per-akun) —
                # ini bikin restart langsung responsif, gak nunggu POLL_INTERVAL penuh
                if stop_event.wait(POLL_INTERVAL):
                    log.info("🔄 restart per-akun diminta — worker berhenti bersih")
                    break
            else:
                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            log.info("👋 BOT STOPPED")
            break

        except Exception as e:
            log.error(f"💥 Unexpected error: {e}")
            log.debug(traceback.format_exc())

            try:
                time.sleep(30)
            except KeyboardInterrupt:
                log.info("👋 BOT STOPPED")
                break


if __name__ == "__main__":
    main()