import os

from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> set[int]:
    return {int(part.strip()) for part in raw.split(",") if part.strip()}


def _parse_display_names(raw: str) -> dict[int, str]:
    names: dict[int, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        uid, _, name = pair.partition(":")
        uid = uid.strip()
        name = name.strip()
        if uid.isdigit() and name:
            names[int(uid)] = name
    return names


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED_USER_IDS = _parse_ids(os.environ.get("ALLOWED_USER_IDS", ""))
DISPLAY_NAMES = _parse_display_names(os.environ.get("DISPLAY_NAMES", ""))

MIMO_API_KEY = os.environ["MIMO_API_KEY"]
MIMO_BASE_URL = os.environ["MIMO_BASE_URL"]
MIMO_MODEL = os.environ.get("MIMO_MODEL", "mimo-v2.5")

# Gemini (Google AI Studio) — основний омні-клієнт (MiMo не тягне українське аудіо).
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")

DB_PATH = os.environ.get("DB_PATH", "companion.db")
REMINDER_HOUR = int(os.environ.get("REMINDER_HOUR", "10"))
MORNING_DIGEST_HOUR = int(os.environ.get("MORNING_DIGEST_HOUR", "9"))

HERMES_ENDPOINT = os.environ.get("HERMES_ENDPOINT", "")


def display_name(user_id: int) -> str:
    return DISPLAY_NAMES.get(user_id, "")


def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USER_IDS
