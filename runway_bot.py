#!/usr/bin/env python3
"""
Edel Runway Multi Account Manager v5.6
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 1 proses, semua akun jalan paralel via thread
✅ accounts.json untuk daftar akun
✅ sessions/<account>.json per akun
✅ Telegram 1 chat, label per akun
✅ Cookie update via Telegram: @acc1 edel_session=eyJ...
✅ /status semua akun sekaligus
"""

import base64
import importlib.util
import json
import os
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
WORKER_FILE = BASE_DIR / "runway_worker.py"
ACCOUNTS_FILE = BASE_DIR / "accounts.json"
SESSIONS_DIR = BASE_DIR / "sessions"
STATES_DIR = BASE_DIR / "states"
BASE_URL = "https://runway.edel.finance"

SESSIONS_DIR.mkdir(exist_ok=True)
STATES_DIR.mkdir(exist_ok=True)

STOP_EVENT = threading.Event()
IMPORT_LOCK = threading.Lock()
MODULES_LOCK = threading.Lock()
WORKER_MODULES: dict[str, object] = {}
WORKER_THREADS: dict[str, threading.Thread] = {}
TG_OFFSET = 0


def now_wib() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d %b %Y, %H:%M:%S WIB")


def log(msg: str):
    print(f"[{now_wib()}] [manager] {msg}", flush=True)


def load_env() -> dict:
    env_path = BASE_DIR / ".env"
    env = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    for k, v in os.environ.items():
        if k in env or k.startswith(("TELEGRAM_", "POLL_", "MAX_API_", "ACCOUNTS", "EDEL_", "RUNWAY_")):
            env[k] = v
    return env


CFG = load_env()
TG_TOKEN = CFG.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID = CFG.get("TELEGRAM_CHAT_ID", "")

TG_HTTP = requests.Session()
TG_HTTP.trust_env = False
TG_GETUPDATES_LONGPOLL = int(CFG.get("TG_GETUPDATES_LONGPOLL", "20"))
TG_GETUPDATES_REQUEST_TIMEOUT = int(CFG.get("TG_GETUPDATES_REQUEST_TIMEOUT", "35"))
TG_SEND_REQUEST_TIMEOUT = int(CFG.get("TG_SEND_REQUEST_TIMEOUT", "20"))
TG_ERROR_COOLDOWN = int(CFG.get("TG_ERROR_COOLDOWN", "600"))
TG_LAST_ERROR_LOG = 0


@dataclass
class AccountConfig:
    id: str
    email: str = ""
    cookie: str = ""


def normalize_account_id(value: str) -> str:
    value = str(value or "").strip().lstrip("@")
    value = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
    return value or "acc"


def parse_accounts_line_mode(text: str) -> dict[str, AccountConfig]:
    """
    Format simpel accounts.json, 3 baris per akun:

    acc1
    akun1@gmail.com
    eyJ...

    acc2
    akun2@gmail.com
    eyJ...

    Baris kosong dan baris komentar diawali # akan di-skip.
    Cookie boleh plain eyJ..., edel_session=eyJ..., atau full cookie header.
    """
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)

    if not lines:
        return {}

    if len(lines) % 3 != 0:
        raise SystemExit(
            "accounts.json format simpel harus 3 baris per akun: id, email, cookie. "
            f"Total baris aktif sekarang: {len(lines)}"
        )

    accounts: dict[str, AccountConfig] = {}
    for i in range(0, len(lines), 3):
        acc_id = normalize_account_id(lines[i])
        email = lines[i + 1].strip()
        cookie = lines[i + 2].strip()
        if not acc_id:
            raise SystemExit(f"accounts.json baris {i + 1}: id akun kosong")
        if acc_id in accounts:
            raise SystemExit(f"accounts.json: id akun dobel: {acc_id}")
        accounts[acc_id] = AccountConfig(id=acc_id, email=email, cookie=cookie)
    return accounts


def parse_accounts_json() -> dict[str, AccountConfig]:
    if not ACCOUNTS_FILE.exists():
        return {}

    text = ACCOUNTS_FILE.read_text(encoding="utf-8").strip()
    if not text:
        return {}

    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return parse_accounts_line_mode(text)
    except Exception as e:
        raise SystemExit(f"accounts.json gagal dibaca: {e}")

    if isinstance(raw, dict) and isinstance(raw.get("accounts"), list):
        rows = raw["accounts"]
    elif isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        rows = []
        for acc_id, item in raw.items():
            if isinstance(item, dict):
                rows.append({"id": acc_id, **item})
    else:
        rows = []

    accounts: dict[str, AccountConfig] = {}
    for idx, item in enumerate(rows, 1):
        if not isinstance(item, dict):
            continue
        acc_id = normalize_account_id(item.get("id") or item.get("name") or f"acc{idx}")
        if acc_id in accounts:
            raise SystemExit(f"accounts.json: id akun dobel: {acc_id}")
        accounts[acc_id] = AccountConfig(
            id=acc_id,
            email=str(item.get("email", "")).strip(),
            cookie=str(item.get("cookie", item.get("edel_session", ""))).strip(),
        )
    return accounts


def load_accounts() -> list[AccountConfig]:
    from_json = parse_accounts_json()
    env_ids = [normalize_account_id(x) for x in CFG.get("ACCOUNTS", "").split(",") if x.strip()]

    if env_ids:
        return [from_json.get(acc_id, AccountConfig(id=acc_id)) for acc_id in env_ids]

    if from_json:
        return list(from_json.values())

    email = CFG.get("RUNWAY_EMAIL", "")
    cookie = CFG.get("EDEL_COOKIE", "")
    if email or cookie:
        return [AccountConfig(id="acc1", email=email, cookie=cookie)]

    raise SystemExit("Tidak ada akun. Isi ACCOUNTS di .env dan accounts.json, contoh: ACCOUNTS=acc1,acc2,acc3")


ACCOUNTS = load_accounts()
ACCOUNTS_BY_ID = {a.id: a for a in ACCOUNTS}


def parse_cookie_string(cookie_str: str, domain: str = "runway.edel.finance") -> list[dict]:
    cookies = []
    cleaned = cookie_str.strip()
    if cleaned.lower().startswith("cookie:"):
        cleaned = cleaned[7:].strip()
    if cleaned.startswith("eyJ") and "=" not in cleaned:
        cleaned = "edel_session=" + cleaned

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


def build_session_state(cookies: list[dict]) -> dict:
    return {"cookies": cookies, "origins": [{"origin": BASE_URL, "localStorage": []}]}


def extract_cookie_from_message(text: str) -> str | None:
    if not text:
        return None
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[7:].strip()
    if text.startswith("eyJ") and len(text) > 20 and " " not in text:
        return "edel_session=" + text
    if "=" in text:
        cookies = parse_cookie_string(text)
        if any(c.get("name") == "edel_session" for c in cookies):
            return "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    return None


def session_file(acc_id: str) -> Path:
    return SESSIONS_DIR / f"{acc_id}.json"


def state_file(acc_id: str) -> Path:
    return STATES_DIR / f"{acc_id}.json"


def get_edel_session_value(acc_id: str) -> str | None:
    p = session_file(acc_id)
    try:
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        for c in data.get("cookies") or []:
            if c.get("name") == "edel_session":
                return c.get("value")
    except Exception:
        return None
    return None


def save_session(acc_id: str, cookie_header: str):
    cookies = parse_cookie_string(cookie_header)
    if not any(c.get("name") == "edel_session" for c in cookies):
        raise ValueError("cookie tidak punya edel_session")
    SESSIONS_DIR.mkdir(exist_ok=True)
    session_file(acc_id).write_text(json.dumps(build_session_state(cookies), indent=2), encoding="utf-8")


def cookie_works(cookie_value: str) -> bool:
    try:
        r = requests.get(
            f"{BASE_URL}/assets",
            headers={"Cookie": cookie_value, "Accept": "application/json", "User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        return r.status_code == 200
    except Exception:
        return False


def decode_session_token(cookie_value: str) -> dict | None:
    if not cookie_value:
        return None
    raw = cookie_value[len("edel_session="):] if cookie_value.startswith("edel_session=") else cookie_value
    part0 = raw.split(".")[0]
    try:
        padded = part0 + "=" * (-len(part0) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None


def get_token_expiry_ts(cookie_value: str) -> float | None:
    data = decode_session_token(cookie_value)
    if not data:
        return None
    try:
        return float(data.get("e")) / 1000
    except Exception:
        return None


def expiry_text(acc_id: str) -> str:
    cookie = get_edel_session_value(acc_id) or ""
    exp_ts = get_token_expiry_ts(cookie)
    if not cookie:
        return "belum ada session"
    if not exp_ts:
        return "session ada, expiry tidak terbaca"
    remaining = exp_ts - datetime.now(timezone.utc).timestamp()
    expire_wib = (datetime.fromtimestamp(exp_ts, tz=timezone.utc) + timedelta(hours=7)).strftime("%d %b %H:%M WIB")
    if remaining < 0:
        return f"EXPIRED ({expire_wib})"
    h = int(remaining // 3600)
    m = int((remaining % 3600) // 60)
    return f"aktif {h}j {m}m, exp {expire_wib}"


def load_state(acc_id: str) -> dict:
    try:
        return json.loads(state_file(acc_id).read_text(encoding="utf-8"))
    except Exception:
        return {}


def status_report(acc_id: str) -> str:
    acc = ACCOUNTS_BY_ID[acc_id]
    state = load_state(acc_id)
    session_status = expiry_text(acc_id)
    round_id = str(state.get("last_round_id", "-"))
    short_round = round_id[:18] + "..." if len(round_id) > 21 else round_id
    status = state.get("last_status", "-")
    last_tick = state.get("last_tick", "-")
    email = f" | {acc.email}" if acc.email else ""
    return f"@{acc_id}{email}\n  session: {session_status}\n  round: {status} / {short_round}\n  tick: {last_tick}"


def ensure_initial_sessions():
    for acc in ACCOUNTS:
        if acc.cookie and acc.cookie != "eyJ...":
            cookie = extract_cookie_from_message(acc.cookie) or acc.cookie
            try:
                save_session(acc.id, cookie)
                log(f"session awal disiapkan dari accounts.json untuk @{acc.id}")
            except Exception as e:
                log(f"cookie awal @{acc.id} gagal disimpan: {e}")


def import_worker_for_account(acc: AccountConfig):
    env_backup = {k: os.environ.get(k) for k in ["EDEL_ACCOUNT_ID", "EDEL_ACCOUNT_EMAIL", "EDEL_MULTI_ACCOUNT_MODE", "EDEL_WORKER_TELEGRAM_POLL"]}
    try:
        os.environ["EDEL_ACCOUNT_ID"] = acc.id
        os.environ["EDEL_ACCOUNT_EMAIL"] = acc.email
        os.environ["EDEL_MULTI_ACCOUNT_MODE"] = "1"
        os.environ["EDEL_WORKER_TELEGRAM_POLL"] = "0"
        module_name = f"runway_worker_{acc.id.replace('.', '_').replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, WORKER_FILE)
        if spec is None or spec.loader is None:
            raise RuntimeError("gagal load runway_worker.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def worker_thread(acc: AccountConfig):
    try:
        with IMPORT_LOCK:
            module = import_worker_for_account(acc)
            with MODULES_LOCK:
                WORKER_MODULES[acc.id] = module
        module.main()
    except SystemExit as e:
        log(f"worker @{acc.id} berhenti: {e}")
    except Exception as e:
        log(f"worker @{acc.id} crash: {e}")
        traceback.print_exc()


def start_workers():
    for acc in ACCOUNTS:
        t = threading.Thread(target=worker_thread, args=(acc,), name=f"edel-{acc.id}", daemon=True)
        WORKER_THREADS[acc.id] = t
        t.start()
        log(f"thread @{acc.id} started")


def send_telegram_reply(chat_id: str, text: str):
    global TG_LAST_ERROR_LOG

    if not TG_TOKEN:
        return

    try:
        TG_HTTP.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=TG_SEND_REQUEST_TIMEOUT,
        )
    except Exception as e:
        now_ts = time.time()
        if now_ts - TG_LAST_ERROR_LOG >= TG_ERROR_COOLDOWN:
            log(f"Telegram sendMessage gagal sementara: {e}")
            TG_LAST_ERROR_LOG = now_ts

def get_telegram_updates(offset: int = 0) -> list:
    global TG_LAST_ERROR_LOG

    if not TG_TOKEN:
        return []

    try:
        r = TG_HTTP.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={
                "offset": offset,
                "timeout": TG_GETUPDATES_LONGPOLL,
                "limit": 10,
                "allowed_updates": ["message"],
            },
            timeout=TG_GETUPDATES_REQUEST_TIMEOUT,
        )

        if r.status_code == 200:
            return r.json().get("result", [])

        now_ts = time.time()
        if now_ts - TG_LAST_ERROR_LOG >= TG_ERROR_COOLDOWN:
            log(f"Telegram getUpdates HTTP {r.status_code} — retry otomatis, worker tetap jalan")
            TG_LAST_ERROR_LOG = now_ts

    except requests.exceptions.ReadTimeout:
        now_ts = time.time()
        if now_ts - TG_LAST_ERROR_LOG >= TG_ERROR_COOLDOWN:
            log(
                "Telegram getUpdates timeout — koneksi Telegram lambat, "
                "command/cookie Telegram mungkin delay, worker tetap jalan"
            )
            TG_LAST_ERROR_LOG = now_ts

    except Exception as e:
        now_ts = time.time()
        if now_ts - TG_LAST_ERROR_LOG >= TG_ERROR_COOLDOWN:
            log(f"Telegram getUpdates error sementara: {e} — retry otomatis")
            TG_LAST_ERROR_LOG = now_ts

    return []

def account_help_text() -> str:
    ids = ", ".join(f"@{a.id}" for a in ACCOUNTS)
    return (
        "👋 *Edel Runway Multi Account*\n\n"
        f"Akun aktif: {ids}\n\n"
        "📌 *Command:*\n"
        "• `/status` — status semua akun\n"
        "• `/status acc1` — status akun tertentu\n"
        "• `/accounts` — list akun\n"
        "• `/vote all` — force vote semua akun\n"
        "• `/vote acc1` — force vote akun tertentu\n\n"
        "🍪 *Update cookie per akun:*\n"
        "• `@acc1 eyJ...`\n"
        "• `@acc1 edel_session=eyJ...`\n"
        "• `@acc1 name1=val1; edel_session=eyJ...`"
    )


def handle_status_command(chat_id: str, parts: list[str]):
    if len(parts) >= 2:
        acc_id = normalize_account_id(parts[1])
        if acc_id not in ACCOUNTS_BY_ID:
            send_telegram_reply(chat_id, f"❌ Akun `{acc_id}` tidak ada.")
            return
        send_telegram_reply(chat_id, "```\n" + status_report(acc_id) + "\n```")
        return
    body = "\n\n".join(status_report(a.id) for a in ACCOUNTS)
    send_telegram_reply(chat_id, "```\n" + body + "\n```")


def handle_vote_command(chat_id: str, parts: list[str]):
    target = normalize_account_id(parts[1]) if len(parts) >= 2 else "all"
    if target in ("all", "semua"):
        targets = [a.id for a in ACCOUNTS]
    else:
        if target not in ACCOUNTS_BY_ID:
            send_telegram_reply(chat_id, f"❌ Akun `{target}` tidak ada.")
            return
        targets = [target]
    with MODULES_LOCK:
        for acc_id in targets:
            module = WORKER_MODULES.get(acc_id)
            if module is not None:
                setattr(module, "_tg_force_vote", True)
    send_telegram_reply(chat_id, "🗳️ Force vote dikirim ke: " + ", ".join(f"@{x}" for x in targets))


def handle_cookie_update(chat_id: str, acc_id: str, cookie_text: str):
    if acc_id not in ACCOUNTS_BY_ID:
        send_telegram_reply(chat_id, f"❌ Akun `@{acc_id}` tidak ada di config.")
        return
    cookie_candidate = extract_cookie_from_message(cookie_text)
    if not cookie_candidate:
        send_telegram_reply(chat_id, f"❌ Format cookie untuk `@{acc_id}` tidak kebaca.")
        return
    send_telegram_reply(chat_id, f"🔄 Verifikasi cookie `@{acc_id}`...")
    if not cookie_works(cookie_candidate):
        send_telegram_reply(chat_id, f"❌ Cookie `@{acc_id}` tidak valid / API menolak.")
        return
    save_session(acc_id, cookie_candidate)
    exp = expiry_text(acc_id)
    log(f"cookie @{acc_id} valid & tersimpan ke {session_file(acc_id).name}")
    send_telegram_reply(chat_id, f"✅ Cookie `@{acc_id}` valid & tersimpan.\n{exp}")


def handle_telegram_message(upd: dict):
    msg = upd.get("message", {})
    text = msg.get("text", "").strip()
    chat_id = str(msg.get("chat", {}).get("id", ""))
    if not text:
        return
    if TG_CHAT_ID and chat_id != str(TG_CHAT_ID):
        log(f"pesan diblokir dari chat_id asing: {chat_id}")
        return
    low = text.lower().strip()
    if low in ("/start", "/help", "help"):
        send_telegram_reply(chat_id, account_help_text())
        return
    if low.startswith("/status"):
        handle_status_command(chat_id, text.split())
        return
    if low == "/accounts":
        lines = [f"@{a.id}" + (f" — {a.email}" if a.email else "") for a in ACCOUNTS]
        send_telegram_reply(chat_id, "*Akun aktif:*\n" + "\n".join(lines))
        return
    if low.startswith("/vote"):
        handle_vote_command(chat_id, text.split())
        return
    m = re.match(r"^@([A-Za-z0-9_.-]+)\s+([\s\S]+)$", text)
    if m:
        handle_cookie_update(chat_id, normalize_account_id(m.group(1)), m.group(2).strip())
        return
    cookie_candidate = extract_cookie_from_message(text)
    if cookie_candidate:
        if len(ACCOUNTS) == 1:
            handle_cookie_update(chat_id, ACCOUNTS[0].id, text)
        else:
            send_telegram_reply(chat_id, "⚠️ Multi akun aktif. Pakai prefix akun, contoh:\n`@acc1 edel_session=eyJ...`")
        return
    if text.startswith("/"):
        send_telegram_reply(chat_id, "❓ Command tidak dikenal. Kirim `/help`.")


def telegram_loop():
    global TG_OFFSET
    if not TG_TOKEN:
        log("Telegram token kosong, listener Telegram dimatikan")
        return
    log("Telegram manager listener aktif")
    while not STOP_EVENT.is_set():
        updates = get_telegram_updates(TG_OFFSET)
        for upd in updates:
            TG_OFFSET = upd.get("update_id", TG_OFFSET) + 1
            handle_telegram_message(upd)
        time.sleep(1)


def manager_main():
    log("── Edel Runway Multi Account Manager v5.8 ──")
    log("akun: " + ", ".join(f"@{a.id}" + (f"({a.email})" if a.email else "") for a in ACCOUNTS))
    ensure_initial_sessions()
    tg = threading.Thread(target=telegram_loop, name="telegram-manager", daemon=True)
    tg.start()
    start_workers()
    try:
        while any(t.is_alive() for t in WORKER_THREADS.values()):
            time.sleep(2)
    except KeyboardInterrupt:
        log("Ctrl+C diterima, stop manager")
        STOP_EVENT.set()

# ──────────────────────────────────────────────
# Integrated Textual UI wrapper
# ──────────────────────────────────────────────

def run_integrated_ui():
    """
    Default mode: terminal dashboard.
    The dashboard runs this same file in --plain mode in the background, so the user
    still only needs one command: python3 runway_bot.py
    """
    try:
        from rich.markup import escape as markup_escape
        from textual.app import App, ComposeResult
        from textual.widgets import Header, Footer, DataTable, RichLog, Label
        from textual.containers import Vertical
    except ImportError:
        print("Dependency UI belum ada. Install dulu:")
        print("python3 -m pip install -r requirements.txt")
        print("\nFallback ke mode log biasa...\n")
        return manager_main()

    import signal as _signal
    import subprocess as _subprocess

    BOT_FILE = Path(__file__).resolve()
    SESSION_RE = re.compile(r"\[(INFO|WARNING|ERROR|DEBUG)\]\s+\[([^\]]+)\]\s*(.*)$")

    def _now_short():
        return datetime.now().strftime("%H:%M:%S")

    def _natural_sort_key(value):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(value))]

    def _read_json(path):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _shorten_round(round_id):
        round_id = str(round_id or "-")
        if len(round_id) > 24:
            return round_id[:10] + "…" + round_id[-8:]
        return round_id

    def _load_ui_accounts():
        accounts = []
        try:
            for acc in ACCOUNTS:
                accounts.append({"id": acc.id, "email": acc.email})
        except Exception:
            accounts = []

        known = {a["id"] for a in accounts}
        for folder in (SESSIONS_DIR, STATES_DIR):
            if folder.exists():
                for f in folder.glob("*.json"):
                    acc_id = normalize_account_id(f.stem)
                    if acc_id not in known:
                        accounts.append({"id": acc_id, "email": ""})
                        known.add(acc_id)

        accounts.sort(key=lambda x: _natural_sort_key(x["id"]))
        return accounts

    def _get_edel_session_value(acc_id):
        data = _read_json(SESSIONS_DIR / f"{acc_id}.json")
        for c in data.get("cookies") or []:
            if c.get("name") == "edel_session":
                return str(c.get("value") or "")
        return ""

    def _ui_decode_session_token(cookie_value):
        if not cookie_value:
            return None
        raw = cookie_value[len("edel_session="):] if cookie_value.startswith("edel_session=") else cookie_value
        part0 = raw.split(".")[0]
        try:
            padded = part0 + "=" * (-len(part0) % 4)
            return json.loads(base64.urlsafe_b64decode(padded))
        except Exception:
            return None

    def _session_status(acc_id):
        cookie = _get_edel_session_value(acc_id)
        if not cookie:
            return "no session"
        data = _ui_decode_session_token(cookie)
        if not data:
            return "session ?"
        try:
            exp_ts = float(data.get("e")) / 1000
        except Exception:
            return "session ?"
        remaining = exp_ts - datetime.now(timezone.utc).timestamp()
        if remaining <= 0:
            return "expired"
        h = int(remaining // 3600)
        m = int((remaining % 3600) // 60)
        return f"ok {h}h {m}m"

    def _parse_account_log_message(msg):
        out = {}
        low = msg.lower()

        if "✅ submitted" in low or "submitted —" in low:
            out["status"] = "✅ Submitted"
            m = re.search(r"Submitted\s+[—-]\s+([^\n]+)", msg)
            if m:
                out["last"] = "Submitted " + m.group(1).strip()
        elif "🟢 calls open" in msg or "bisa pilih" in low:
            out["status"] = "🟢 Calls open"
        elif "▶️ starting" in low or "starting" == low.strip():
            out["status"] = "▶️ Starting"
        elif "✅ started" in low:
            out["status"] = "✅ Started"
        elif "belum ada round" in low:
            out["status"] = "⚪ No round"
        elif "allocation pending" in low or "lock_pending" in low or "lock in progress" in low:
            out["status"] = "⏳ Allocation pending"
        elif "already committed" in low:
            out["status"] = "🔒 Committed"
        elif "settlement server masih pending" in low or "settlement bug" in low:
            out["status"] = "⏳ Settlement pending"
        elif "cookie expired" in low:
            out["status"] = "🍪 Cookie expired"
        elif "api error" in low:
            out["status"] = "❌ API error"
        elif "start failed" in low:
            out["status"] = "❌ Start failed"
        elif "502" in low or "504" in low:
            out["status"] = "⏳ server retry"
        elif "timeout" in low:
            out["status"] = "⏳ Timeout retry"

        if "settlement eligible:" in low:
            out["settle"] = msg.split(":", 1)[1].strip()
        if "status:" in low:
            val = msg.split(":", 1)[1].strip()
            if val:
                out["status"] = val

        m = re.search(r"window tutup\s+([^|]+)", msg, flags=re.I)
        if m:
            out["window"] = m.group(1).strip()
        m = re.search(r"estimasi settle\s+(.+)$", msg, flags=re.I)
        if m:
            out["settle"] = m.group(1).strip()
        m = re.search(r"settle\s+([^|]+)\s*\|\s*window tutup\s+(.+)$", msg, flags=re.I)
        if m:
            out["settle"] = m.group(1).strip()
            out["window"] = m.group(2).strip()
        return out

    class EdelRunwayUI(App):
        CSS = """
        Screen { layout: vertical; }
        #top_panel { height: 42%; border-bottom: solid green; }
        #bottom_panel { height: 58%; layout: vertical; }
        #log_title { text-align: center; background: $boost; color: yellow; text-style: bold; }
        DataTable { height: 100%; }
        RichLog { height: 100%; border: solid $primary; }
        """

        BINDINGS = [
            ("q", "quit", "Quit"),
            ("s", "start_bot", "Start"),
            ("x", "stop_bot", "Stop"),
            ("r", "restart_bot", "Restart"),
            ("a", "show_all_logs", "All Logs"),
            ("c", "clear_log", "Clear Log"),
        ]

        def __init__(self):
            super().__init__()
            self.accounts = _load_ui_accounts()
            self.account_ids = [a["id"] for a in self.accounts]
            self.account_email = {a["id"]: a.get("email", "") for a in self.accounts}
            self.stats = {}
            self.logs_db = {"ALL": []}
            self.active_account = self.account_ids[0] if self.account_ids else "ALL"
            self.process = None
            self.reader_thread = None
            self.stop_reader = threading.Event()

            for acc_id in self.account_ids:
                self.stats[acc_id] = {
                    "account": acc_id,
                    "email": self.account_email.get(acc_id, ""),
                    "session": "-",
                    "worker": "Waiting",
                    "status": "-",
                    "round": "-",
                    "window": "-",
                    "settle": "-",
                    "updated": "-",
                    "last": "-",
                }
                self.logs_db[acc_id] = []

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical(id="top_panel"):
                yield DataTable(id="accounts_table")
            with Vertical(id="bottom_panel"):
                yield Label("S=start | X=stop | R=restart | A=all logs | C=clear | Q=quit", id="log_title")
                yield RichLog(id="account_log", highlight=True, markup=True, max_lines=1500)
            yield Footer()

        def on_mount(self):
            table = self.query_one("#accounts_table", DataTable)
            table.cursor_type = "row"
            for name, key in [
                ("Account", "account"), ("Email", "email"), ("Session", "session"),
                ("Worker", "worker"), ("Status", "status"), ("Round", "round"),
                ("Window", "window"), ("Settle", "settle"), ("Updated", "updated"),
                ("Last Message", "last"),
            ]:
                table.add_column(name, key=key)

            if not self.account_ids:
                self.query_one("#log_title", Label).update("[red]accounts.json tidak kebaca[/red]")
                self.add_log("ALL", "accounts.json tidak kebaca atau kosong")
                return

            for acc_id in self.account_ids:
                st = self.stats[acc_id]
                table.add_row(
                    st["account"], st["email"], st["session"], st["worker"], st["status"],
                    st["round"], st["window"], st["settle"], st["updated"], st["last"],
                    key=acc_id,
                )

            self.update_title()
            self.set_interval(2.0, self.refresh_from_files)
            self.start_bot()

        def update_title(self):
            name = self.active_account
            if name == "ALL":
                text = "--- LOGS FOR: [cyan]ALL ACCOUNTS[/cyan] ---"
            else:
                email = self.account_email.get(name, "")
                suffix = f" / {markup_escape(email)}" if email else ""
                text = f"--- LOGS FOR: [cyan]{markup_escape(name)}[/cyan]{suffix} ---"
            self.query_one("#log_title", Label).update(text)

        def refresh_from_files(self):
            for acc_id in self.account_ids:
                st = self.stats[acc_id]
                st["session"] = _session_status(acc_id)
                state = _read_json(STATES_DIR / f"{acc_id}.json")
                if state:
                    status = state.get("last_status") or st.get("status") or "-"
                    st["status"] = str(status)
                    st["round"] = _shorten_round(state.get("last_round_id") or st.get("round") or "-")
                    st["updated"] = str(state.get("last_tick") or st.get("updated") or "-")
                self.update_account_row(acc_id)

        def update_account_row(self, acc_id):
            table = self.query_one("#accounts_table", DataTable)
            st = self.stats.get(acc_id)
            if not st:
                return
            for key in ["account", "email", "session", "worker", "status", "round", "window", "settle", "updated", "last"]:
                try:
                    table.update_cell(acc_id, key, str(st.get(key, "-")))
                except Exception:
                    pass

        def add_log(self, acc_id, message):
            clean = str(message).rstrip("\n")
            if not clean:
                return
            formatted = f"[{_now_short()}] [{acc_id}] {clean}"
            self.logs_db.setdefault(acc_id, []).append(formatted)
            self.logs_db[acc_id] = self.logs_db[acc_id][-1000:]
            self.logs_db.setdefault("ALL", []).append(formatted)
            self.logs_db["ALL"] = self.logs_db["ALL"][-2500:]
            if self.active_account in (acc_id, "ALL"):
                self.query_one("#account_log", RichLog).write(markup_escape(formatted))

        def redraw_log(self):
            log_widget = self.query_one("#account_log", RichLog)
            log_widget.clear()
            for msg in self.logs_db.get(self.active_account, []):
                log_widget.write(markup_escape(msg))

        def parse_and_apply_line(self, line):
            m = SESSION_RE.search(line)
            if not m:
                self.add_log("ALL", line)
                return

            level, acc_id, msg = m.groups()
            acc_id = normalize_account_id(acc_id)

            if acc_id not in self.stats and acc_id != "manager":
                self.stats[acc_id] = {
                    "account": acc_id, "email": "", "session": "-", "worker": "Running",
                    "status": "-", "round": "-", "window": "-", "settle": "-",
                    "updated": "-", "last": "-",
                }
                self.logs_db[acc_id] = []
                self.account_ids.append(acc_id)
                try:
                    table = self.query_one("#accounts_table", DataTable)
                    st = self.stats[acc_id]
                    table.add_row(
                        st["account"], st["email"], st["session"], st["worker"], st["status"],
                        st["round"], st["window"], st["settle"], st["updated"], st["last"],
                        key=acc_id,
                    )
                except Exception:
                    pass

            if acc_id == "manager":
                self.add_log("ALL", msg)
                return

            st = self.stats[acc_id]
            st["worker"] = "Running"
            st["updated"] = _now_short()
            st["last"] = msg[:160]
            if level in ("WARNING", "ERROR"):
                st["worker"] = level

            parsed = _parse_account_log_message(msg)
            for k, v in parsed.items():
                st[k] = v

            self.add_log(acc_id, msg)
            self.update_account_row(acc_id)

        def start_bot(self):
            if self.process and self.process.poll() is None:
                self.add_log("ALL", "bot sudah jalan")
                return

            self.stop_reader.clear()
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["RUNWAY_FORCE_PLAIN"] = "1"

            try:
                self.process = _subprocess.Popen(
                    [sys.executable, "-u", str(BOT_FILE), "--plain"],
                    cwd=str(BASE_DIR),
                    stdout=_subprocess.PIPE,
                    stderr=_subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env,
                )
            except Exception as e:
                self.add_log("ALL", f"gagal start bot: {e}")
                return

            for acc_id in self.account_ids:
                self.stats[acc_id]["worker"] = "Starting"
                self.update_account_row(acc_id)

            self.add_log("ALL", "▶️ manager started (--plain)")
            self.reader_thread = threading.Thread(target=self.read_process_output, daemon=True)
            self.reader_thread.start()

        def read_process_output(self):
            try:
                if not self.process or not self.process.stdout:
                    return
                for line in self.process.stdout:
                    if self.stop_reader.is_set():
                        break
                    line = line.rstrip()
                    if not line:
                        continue
                    self.call_from_thread(self.parse_and_apply_line, line)
            except Exception as e:
                self.call_from_thread(self.add_log, "ALL", f"reader error: {e}")
            finally:
                code = self.process.poll() if self.process else None
                self.call_from_thread(self.on_process_exit, code)

        def on_process_exit(self, code):
            for acc_id in self.account_ids:
                if self.stats[acc_id].get("worker") not in ("Stopped",):
                    self.stats[acc_id]["worker"] = f"Exited {code}"
                    self.update_account_row(acc_id)
            self.add_log("ALL", f"🛑 manager exited code={code}")

        def stop_bot_process(self):
            self.stop_reader.set()
            if self.process and self.process.poll() is None:
                try:
                    self.process.send_signal(_signal.SIGINT)
                    time.sleep(1.5)
                    if self.process.poll() is None:
                        self.process.kill()
                except Exception:
                    pass

        def action_start_bot(self):
            self.start_bot()

        def action_stop_bot(self):
            self.add_log("ALL", "⏹ stop requested")
            self.stop_bot_process()
            for acc_id in self.account_ids:
                self.stats[acc_id]["worker"] = "Stopped"
                self.update_account_row(acc_id)

        def action_restart_bot(self):
            self.add_log("ALL", "🔄 restart requested")
            self.stop_bot_process()
            time.sleep(1)
            self.start_bot()

        def action_show_all_logs(self):
            self.active_account = "ALL"
            self.update_title()
            self.redraw_log()

        def action_clear_log(self):
            self.logs_db[self.active_account] = []
            self.redraw_log()

        def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
            try:
                acc_id = event.row_key.value
                self.active_account = acc_id
                self.update_title()
                self.redraw_log()
            except Exception:
                pass

        def on_exit(self):
            self.stop_bot_process()

    app = EdelRunwayUI()
    app.run()


def entrypoint():
    if "--plain" in sys.argv or "--no-ui" in sys.argv or os.environ.get("RUNWAY_FORCE_PLAIN") == "1":
        return manager_main()
    return run_integrated_ui()


if __name__ == "__main__":
    entrypoint()
