"""aiosqlite CRUD для shelf / profiles / reflections / messages + знімок стану по user_id."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_KYIV_TZ = ZoneInfo("Europe/Kyiv")


def _parse_remind_at(remind_at_str: str) -> datetime | None:
    """Parse remind_at string to aware UTC datetime.

    AI often stores Kyiv time without timezone marker, so naive datetimes
    are treated as Kyiv (UTC+3) and converted to UTC.
    """
    try:
        dt = datetime.fromisoformat(remind_at_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_KYIV_TZ)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _normalize_remind_at(remind_at_str: str | None) -> str | None:
    """Normalize remind_at to UTC ISO string with +00:00 suffix for DB storage.

    Storing with explicit UTC offset lets _parse_remind_at distinguish
    already-normalized values from legacy Kyiv-time values.
    """
    if not remind_at_str:
        return None
    dt = _parse_remind_at(remind_at_str)
    if dt is None:
        return remind_at_str
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")

import aiosqlite

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Скільки рядків messages тримати на user_id (~10 ходів = 10 user + 10 assistant)
MAX_HISTORY_ROWS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        await db.commit()


# --- shelf ---------------------------------------------------------------

async def add_shelf_item(db_path: str, user_id: int, item_type: str, content: str, source: str | None, remind_at: str | None = None, recurring: str | None = None) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO shelf (user_id, created, type, content, status, source, remind_at, recurring) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
            (user_id, _now(), item_type, content, source, _normalize_remind_at(remind_at), recurring),
        )
        await db.commit()
        return cur.lastrowid


async def get_shelf_item(db_path: str, item_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM shelf WHERE id = ?", (item_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def set_shelf_status(db_path: str, item_id: int, status: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE shelf SET status = ? WHERE id = ?", (status, item_id))
        await db.commit()


async def set_shelf_reminded(db_path: str, item_id: int, when: str | None = None) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE shelf SET reminded = ? WHERE id = ?", (when or _now(), item_id))
        await db.commit()


async def get_active_shelf(db_path: str, user_id: int, limit: int = 30) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM shelf WHERE user_id = ? AND status = 'active' ORDER BY created DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(row) for row in await cur.fetchall()]


async def get_shelf_by_statuses(db_path: str, user_id: int, statuses: list[str], limit: int = 10) -> list[dict]:
    placeholders = ",".join("?" for _ in statuses)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM shelf WHERE user_id = ? AND status IN ({placeholders}) ORDER BY created DESC LIMIT ?",
            (user_id, *statuses, limit),
        )
        return [dict(row) for row in await cur.fetchall()]


async def get_stale_tasks(db_path: str, user_id: int, days: int = 7) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM shelf
               WHERE user_id = ? AND type = 'task' AND status = 'active'
                 AND created <= ?
                 AND (reminded IS NULL OR reminded = '' OR reminded <= ?)
               ORDER BY created ASC""",
            (user_id, cutoff, cutoff),
        )
        return [dict(row) for row in await cur.fetchall()]


# --- profiles --------------------------------------------------------------

async def add_profile(db_path: str, user_id: int, query: str, criteria: str | None) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO profiles (user_id, created, query, criteria, status) VALUES (?, ?, ?, ?, 'active')",
            (user_id, _now(), query, criteria),
        )
        await db.commit()
        return cur.lastrowid


async def get_active_profiles(db_path: str, user_id: int) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM profiles WHERE user_id = ? AND status = 'active' ORDER BY created DESC",
            (user_id,),
        )
        return [dict(row) for row in await cur.fetchall()]


# --- reflections -------------------------------------------------------------

async def add_reflection(db_path: str, user_id: int, question: str | None, answer: str | None) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO reflections (user_id, created, question, answer) VALUES (?, ?, ?, ?)",
            (user_id, _now(), question, answer),
        )
        await db.commit()
        return cur.lastrowid


async def get_recent_reflections(db_path: str, user_id: int, limit: int = 5) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM reflections WHERE user_id = ? ORDER BY created DESC LIMIT ?",
            (user_id, limit),
        )
        rows = [dict(row) for row in await cur.fetchall()]
        rows.reverse()
        return rows


# --- messages ----------------------------------------------------------------

async def add_message(db_path: str, user_id: int, chat_id: int, role: str, content: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO messages (user_id, chat_id, role, content, created) VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, role, content, _now()),
        )
        await db.execute(
            """DELETE FROM messages WHERE user_id = ? AND id NOT IN (
                   SELECT id FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?
               )""",
            (user_id, user_id, MAX_HISTORY_ROWS),
        )
        await db.commit()


async def get_recent_messages(db_path: str, user_id: int, limit: int = MAX_HISTORY_ROWS) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        rows = [dict(row) for row in await cur.fetchall()]
        rows.reverse()
        return rows


# --- snapshot ------------------------------------------------------------------

_TYPE_LABELS = {"task": "завдання", "idea": "ідея", "thought": "думка"}


def format_shelf_list(items: list[dict]) -> str:
    lines = []
    for item in items:
        label = _TYPE_LABELS.get(item["type"], item["type"])
        lines.append(f"- [{item['id']}] ({label}, {item['status']}) {item['content']}")
    return "\n".join(lines)


async def build_snapshot(db_path: str, user_id: int) -> str:
    shelf = await get_active_shelf(db_path, user_id)
    reflections = await get_recent_reflections(db_path, user_id, limit=5)
    profiles = await get_active_profiles(db_path, user_id)

    parts = ["=== ЗНІМОК СТАНУ ==="]

    parts.append("Активна полиця:")
    if shelf:
        parts.append(format_shelf_list(shelf))
    else:
        parts.append("Полиця порожня.")

    parts.append("Останні рефлексії:")
    if reflections:
        for r in reflections:
            parts.append(f"- Питання: {r['question']} / Відповідь: {r['answer']}")
    else:
        parts.append("Рефлексій ще нема.")

    parts.append("Активні профілі стеження:")
    if profiles:
        for p in profiles:
            criteria = f", критерії: {p['criteria']}" if p["criteria"] else ""
            parts.append(f"- [{p['id']}] {p['query']}{criteria}")
    else:
        parts.append("Немає активних профілів стеження.")

    return "\n".join(parts)


# --- scheduled reminders --------------------------------------------------

async def get_due_reminders(db_path: str, user_id: int) -> list[dict]:
    now_utc = datetime.now(timezone.utc)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM shelf
               WHERE user_id = ? AND remind_at IS NOT NULL
                 AND status = 'active' AND reminded IS NULL
               ORDER BY remind_at ASC""",
            (user_id,),
        )
        rows = [dict(row) for row in await cur.fetchall()]
    # Python-level comparison to handle timezone-naive remind_at correctly
    return [r for r in rows if (dt := _parse_remind_at(r["remind_at"])) and dt <= now_utc]


async def get_today_items(db_path: str, user_id: int) -> dict:
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat()
    today_end = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur1 = await db.execute(
            "SELECT * FROM shelf WHERE user_id=? AND status='active' AND type='task' AND remind_at BETWEEN ? AND ? ORDER BY remind_at",
            (user_id, today_start, today_end))
        today = [dict(r) for r in await cur1.fetchall()]
        cur2 = await db.execute(
            "SELECT * FROM shelf WHERE user_id=? AND status='active' AND type='task' AND remind_at IS NULL ORDER BY created LIMIT 5",
            (user_id,))
        pending = [dict(r) for r in await cur2.fetchall()]
        cur3 = await db.execute(
            "SELECT * FROM shelf WHERE user_id=? AND status='active' AND type='idea' ORDER BY created DESC LIMIT 3",
            (user_id,))
        ideas = [dict(r) for r in await cur3.fetchall()]
        return {"today": today, "pending": pending, "ideas": ideas}


async def set_shelf_remind_at(db_path: str, item_id: int, remind_at: str | None) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE shelf SET remind_at = ? WHERE id = ?", (_normalize_remind_at(remind_at), item_id))
        await db.commit()


async def update_shelf_reminded(db_path: str, item_id: int, reminded: str | None) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE shelf SET reminded = ? WHERE id = ?", (reminded, item_id))
        await db.commit()
