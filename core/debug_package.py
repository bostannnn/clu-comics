"""
Debug Package builder.

Bundles the most useful diagnostic data (config, settings, recent logs and
system info) into a single in-memory ZIP so users can attach it to a support
request. All secrets are redacted by default using the same masking the app
uses elsewhere for credentials, so the package is safe to share publicly.

Contents of the ZIP:
    README.txt          -- what's inside + redaction notice
    system_info.json    -- version, platform, python, redacted paths, flags
    config.ini          -- copy of config.ini with secrets redacted
    db_settings.json    -- user_preferences rows + table list (settings only)
    logs/app.log        -- redacted last N lines of app.log
    logs/monitor.log    -- redacted last N lines of monitor.log
"""
import configparser
import io
import json
import os
import platform
import re
import sys
import zipfile
from collections import deque

from core.app_logging import APP_LOG, MONITOR_LOG, LOG_DIR, app_logger
from core.config import CONFIG_DIR, CONFIG_FILE
from core.version import __version__
from models.providers.crypto import mask_credential

# Substrings that mark a key as sensitive. Compared against a normalized key
# (uppercased, separators stripped) so "CF-Access-Client-Id", "client_id" and
# "ClientId" all match. Substring matching keeps this future-proof as new secret
# settings appear (anything with KEY / PASSWORD / TOKEN / SECRET / CLIENTID).
_SENSITIVE_SUBSTRINGS = ("KEY", "PASSWORD", "TOKEN", "SECRET", "CLIENTID", "CREDENTIAL")

# Explicit (normalized) keys that don't contain an obvious marker but are still sensitive.
_SENSITIVE_EXPLICIT = {"METRONUSERNAME", "CLUUSERNAME"}

# Matches a JSON/dict-style `"key": "value"` pair, tolerant of any level of
# backslash-escaping (none for config.ini values, single/multiple for
# JSON-encoded DB preferences). Used to mask secrets nested inside a value
# whose own top-level key looks innocuous (e.g. custom_headers / HEADERS).
_BLOB_KV_RE = re.compile(r'(\\*"([^"\\]+?)\\*"\s*:\s*\\*")([^"\\]*)(\\*")')
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Z0-9_.-]*(?:KEY|PASSWORD|TOKEN|SECRET|CLIENT[-_]?ID|CREDENTIAL)"
    r"[A-Z0-9_.-]*\s*[=:]\s*)([^\s,;&]+)"
)
_URL_RE = re.compile(r"https?://[^\s<>'\")]+")
_UNIX_PATH_RE = re.compile(
    r"(^|(?<=[\s=:,\[\(\"']))/(?!/)(?=[^\s\"'<>,;\r\n])[^\"'<>,;\r\n]*",
    re.MULTILINE,
)
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\"'<>,;\r\n]+")
_WINDOWS_UNC_PATH_RE = re.compile(r"\\\\[^\"'<>,;\r\n]+")

# How many trailing lines of each log file to include.
LOG_TAIL_LINES = 5000


def _is_sensitive_key(key: str) -> bool:
    """True when a config/preference key holds a secret that must be redacted."""
    if not key:
        return False
    norm = re.sub(r"[^A-Z0-9]", "", key.upper())
    if norm in _SENSITIVE_EXPLICIT:
        return True
    return any(marker in norm for marker in _SENSITIVE_SUBSTRINGS)


def _redact_blob(value):
    """Mask secrets nested inside a string value (JSON / header dict / etc.).

    Scans for `"key": "value"` pairs and masks the value whenever the nested
    key is sensitive, leaving the surrounding structure intact. No-op for
    strings that contain no such pairs.
    """
    if not isinstance(value, str) or not value:
        return value

    def _repl(m):
        if _is_sensitive_key(m.group(2)) and m.group(3):
            return m.group(1) + mask_credential(m.group(3)) + m.group(4)
        return m.group(0)

    return _BLOB_KV_RE.sub(_repl, value)


def _sanitize_value(key, value):
    """Mask the whole value when its key is sensitive, else scrub nested secrets."""
    if not isinstance(value, str):
        return value
    if _is_sensitive_key(key):
        return mask_credential(value) if value else value
    return _redact_paths(_redact_blob(value))


def _redact_paths(text: str) -> str:
    """Replace local filesystem paths with a placeholder."""
    if not text:
        return text
    text = _WINDOWS_UNC_PATH_RE.sub("[PATH REDACTED]", text)
    text = _WINDOWS_PATH_RE.sub("[PATH REDACTED]", text)
    text = _UNIX_PATH_RE.sub("[PATH REDACTED]", text)
    return text


def _redact_log_text(text: str) -> str:
    """Redact sensitive values, URLs and local filesystem paths from log text."""
    if not text:
        return text

    text = _redact_blob(text)

    def _mask_assignment(m):
        return m.group(1) + mask_credential(m.group(2))

    text = _SENSITIVE_ASSIGNMENT_RE.sub(_mask_assignment, text)
    text = _URL_RE.sub("[URL REDACTED]", text)
    return _redact_paths(text)


def _redacted_config_ini(config_path: str = CONFIG_FILE) -> str:
    """Return config.ini as a string with all sensitive values masked."""
    if not os.path.exists(config_path):
        return f"# config.ini not found at {_redact_paths(config_path)}\n"

    parser = configparser.RawConfigParser()
    parser.optionxform = str  # preserve key case
    try:
        parser.read(config_path)
    except configparser.Error as e:
        return f"# Failed to parse config.ini: {e}\n"

    for section in parser.sections():
        for key, value in parser.items(section):
            sanitized = _sanitize_value(key, value)
            if sanitized != value:
                parser.set(section, key, sanitized)

    buf = io.StringIO()
    parser.write(buf)
    return buf.getvalue()


def _db_settings_json() -> str:
    """Dump the user_preferences table + table list as JSON (secrets redacted)."""
    from core.database import get_db_connection

    data = {"user_preferences": [], "tables": []}
    conn = None
    try:
        conn = get_db_connection()
        if conn is None:
            return json.dumps({"error": "database unavailable"}, indent=2)
        c = conn.cursor()

        c.execute(
            "SELECT key, value, category, updated_at FROM user_preferences ORDER BY key"
        )
        for row in c.fetchall():
            key = row["key"]
            data["user_preferences"].append({
                "key": key,
                "value": _sanitize_value(key, row["value"]),
                "category": row["category"],
                "updated_at": row["updated_at"],
            })

        c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        data["tables"] = [r["name"] for r in c.fetchall()]
    except Exception as e:
        app_logger.error(f"Debug package: failed reading db settings: {e}")
        data["error"] = str(e)
    finally:
        if conn is not None:
            conn.close()

    return json.dumps(data, indent=2, default=str)


def _system_info_json() -> str:
    """Return non-sensitive runtime/environment info as JSON."""
    def _redacted_path(value):
        return "[PATH REDACTED]" if value else value

    info = {
        "version": __version__,
        "platform": platform.platform(),
        "python_version": sys.version,
        "paths": {
            "config_dir": _redacted_path(CONFIG_DIR),
            "log_dir": _redacted_path(LOG_DIR),
            "config_file": _redacted_path(CONFIG_FILE),
        },
        "flags": {
            "MONITOR": os.environ.get("MONITOR", ""),
            "ENABLE_DEBUG_LOGGING": os.environ.get("ENABLE_DEBUG_LOGGING", ""),
            "PUID": os.environ.get("PUID", ""),
            "PGID": os.environ.get("PGID", ""),
        },
    }
    return json.dumps(info, indent=2, default=str)


def _tail(path: str, lines: int = LOG_TAIL_LINES) -> str:
    """Return the last ``lines`` lines of a text file, or a placeholder."""
    if not path or not os.path.exists(path):
        return f"(log file not found: {path})\n"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=lines)
        return "".join(tail)
    except Exception as e:
        return f"(failed to read log {path}: {e})\n"


def _redacted_tail(path: str, lines: int = LOG_TAIL_LINES) -> str:
    """Return a redacted tail suitable for support ZIPs."""
    return _redact_log_text(_tail(path, lines=lines))


def _readme() -> str:
    """Short explanation of the package contents and the redaction guarantee."""
    return (
        "CLU Debug Package\n"
        "=================\n\n"
        f"Generated by Comic Library Utilities v{__version__}.\n\n"
        "Contents:\n"
        "  system_info.json  - version, platform, python, redacted paths, flags\n"
        "  config.ini        - your config with secrets and paths masked\n"
        "  db_settings.json  - user_preferences (settings) + table list, redacted\n"
        "  logs/app.log      - redacted last {n} lines of the application log\n"
        "  logs/monitor.log  - redacted last {n} lines of the monitor log\n\n"
        "Redaction:\n"
        "  API keys, passwords and tokens are masked (e.g. 'abcd...wxyz').\n"
        "  Log URLs and local filesystem paths are replaced with placeholders.\n"
        "  No reading history, file paths or full database is included.\n"
        "  This package is safe to attach to a public support request.\n"
    ).format(n=LOG_TAIL_LINES)


def build_debug_package() -> bytes:
    """Build the debug package ZIP and return its raw bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", _readme())
        zf.writestr("system_info.json", _system_info_json())
        zf.writestr("config.ini", _redacted_config_ini())
        zf.writestr("db_settings.json", _db_settings_json())
        zf.writestr("logs/app.log", _redacted_tail(APP_LOG))
        zf.writestr("logs/monitor.log", _redacted_tail(MONITOR_LOG))
    buf.seek(0)
    return buf.getvalue()
