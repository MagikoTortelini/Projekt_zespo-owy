import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "study_planner.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                google_event_id TEXT UNIQUE,
                title TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                location TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                preferred_start_hour INTEGER NOT NULL,
                preferred_end_hour INTEGER NOT NULL,
                block_minutes INTEGER NOT NULL,
                break_minutes INTEGER NOT NULL,
                max_daily_study_minutes INTEGER NOT NULL
            )
            """
        )

        # Jeden rekord dla jednego użytkownika / jednej instalacji
        existing = conn.execute(
            "SELECT id FROM user_preferences WHERE id = 1"
        ).fetchone()

        if not existing:
            conn.execute(
                """
                INSERT INTO user_preferences (
                    id,
                    preferred_start_hour,
                    preferred_end_hour,
                    block_minutes,
                    break_minutes,
                    max_daily_study_minutes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (1, 8, 20, 90, 15, 240),
            )

        conn.commit()


def clear_calendar_events():
    with get_connection() as conn:
        conn.execute("DELETE FROM calendar_events")
        conn.commit()


def insert_imported_events(events: list[dict]):
    with get_connection() as conn:
        for event in events:
            conn.execute(
                """
                INSERT INTO calendar_events (
                    google_event_id,
                    title,
                    start_time,
                    end_time,
                    location
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.get("id"),
                    event.get("summary") or "(bez nazwy)",
                    event.get("start") or "",
                    event.get("end") or "",
                    event.get("location") or "",
                ),
            )
        conn.commit()


def get_calendar_events():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, google_event_id, title, start_time, end_time, location
            FROM calendar_events
            ORDER BY start_time
            """
        ).fetchall()

    return [dict(row) for row in rows]


def calendar_event_exists(title: str, start_time: str, end_time: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM calendar_events
            WHERE title = ? AND start_time = ? AND end_time = ?
            LIMIT 1
            """,
            (title, start_time, end_time),
        ).fetchone()

    return row is not None


def insert_calendar_event(event: dict):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO calendar_events (
                google_event_id,
                title,
                start_time,
                end_time,
                location
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                event.get("id"),
                event.get("summary") or "(bez nazwy)",
                event.get("start") or "",
                event.get("end") or "",
                event.get("location") or "",
            ),
        )
        conn.commit()


def get_user_preferences():
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                preferred_start_hour,
                preferred_end_hour,
                block_minutes,
                break_minutes,
                max_daily_study_minutes
            FROM user_preferences
            WHERE id = 1
            """
        ).fetchone()

    if not row:
        return {
            "preferred_start_hour": 8,
            "preferred_end_hour": 20,
            "block_minutes": 90,
            "break_minutes": 15,
            "max_daily_study_minutes": 240,
        }

    return dict(row)


def save_user_preferences(preferences: dict):
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE user_preferences
            SET
                preferred_start_hour = ?,
                preferred_end_hour = ?,
                block_minutes = ?,
                break_minutes = ?,
                max_daily_study_minutes = ?
            WHERE id = 1
            """,
            (
                int(preferences["preferred_start_hour"]),
                int(preferences["preferred_end_hour"]),
                int(preferences["block_minutes"]),
                int(preferences["break_minutes"]),
                int(preferences["max_daily_study_minutes"]),
            ),
        )
        conn.commit()