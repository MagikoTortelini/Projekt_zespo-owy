import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "study_planner.db"


DEFAULT_PREFERENCES = {
    "preferred_start_hour": 8,
    "preferred_end_hour": 20,
    "block_minutes": 90,
    "break_minutes": 15,
    "max_daily_study_minutes": 240,
    "study_location": "",
    "solo_preference_mode": "hard",
    "group_preference_mode": "hard",
    "commute_extra_buffer_minutes": 10,
}


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row):
    return dict(row) if row else None


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def get_columns(conn, table_name: str) -> set[str]:
    if not table_exists(conn, table_name):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def migrate_users_table(conn):
    columns = get_columns(conn, "users")

    if "google_sub" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN google_sub TEXT")

    if "email" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")

    if "last_login_at" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")


def create_google_credentials_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS google_credentials (
            user_id INTEGER PRIMARY KEY,
            token TEXT NOT NULL,
            refresh_token TEXT,
            token_uri TEXT NOT NULL DEFAULT 'https://oauth2.googleapis.com/token',
            scopes TEXT,
            expiry TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )


def create_notifications_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            plan_id INTEGER,
            read BOOLEAN NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (plan_id) REFERENCES study_plans(id) ON DELETE CASCADE
        )
        """
    )


def normalize_optional_text(value):
    value = (value or "").strip()
    return value or None


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def normalize_preference_mode(value: str | None) -> str:
    mode = (value or "").strip().lower()
    return mode if mode in {"hard", "soft"} else "hard"


def migrate_user_preferences_columns(conn):
    columns = get_columns(conn, "user_preferences")

    if "study_location" not in columns:
        conn.execute("ALTER TABLE user_preferences ADD COLUMN study_location TEXT NOT NULL DEFAULT ''")

    if "solo_preference_mode" not in columns:
        conn.execute("ALTER TABLE user_preferences ADD COLUMN solo_preference_mode TEXT NOT NULL DEFAULT 'hard'")

    if "group_preference_mode" not in columns:
        conn.execute("ALTER TABLE user_preferences ADD COLUMN group_preference_mode TEXT NOT NULL DEFAULT 'hard'")

    if "commute_extra_buffer_minutes" not in columns:
        conn.execute("ALTER TABLE user_preferences ADD COLUMN commute_extra_buffer_minutes INTEGER NOT NULL DEFAULT 10")


def init_db():
    with get_connection() as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                google_sub TEXT,
                email TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login_at TEXT
            )
            """
        )
        migrate_users_table(conn)
        create_google_credentials_table(conn)

        # Od tego etapu nie tworzymy już lokalnego/pseudo użytkownika typu "Użytkownik 1".
        # Użytkownik aplikacji powstaje dopiero po poprawnym logowaniu Google.
        migrate_user_preferences(conn)
        migrate_calendar_events(conn)
        migrate_study_plans_and_blocks(conn)
        create_notifications_table(conn)

        conn.commit()


def migrate_user_preferences(conn):
    if not table_exists(conn, "user_preferences"):
        conn.execute(
            """
            CREATE TABLE user_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                preferred_start_hour INTEGER NOT NULL,
                preferred_end_hour INTEGER NOT NULL,
                block_minutes INTEGER NOT NULL,
                break_minutes INTEGER NOT NULL,
                max_daily_study_minutes INTEGER NOT NULL,
                study_location TEXT NOT NULL DEFAULT '',
                solo_preference_mode TEXT NOT NULL DEFAULT 'hard',
                group_preference_mode TEXT NOT NULL DEFAULT 'hard',
                commute_extra_buffer_minutes INTEGER NOT NULL DEFAULT 10,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        return

    columns = get_columns(conn, "user_preferences")
    if "user_id" in columns:
        migrate_user_preferences_columns(conn)
        return

    old_rows = conn.execute(
        """
        SELECT
            preferred_start_hour,
            preferred_end_hour,
            block_minutes,
            break_minutes,
            max_daily_study_minutes
        FROM user_preferences
        """
    ).fetchall()

    conn.execute("ALTER TABLE user_preferences RENAME TO user_preferences_old")
    conn.execute(
        """
        CREATE TABLE user_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            preferred_start_hour INTEGER NOT NULL,
            preferred_end_hour INTEGER NOT NULL,
            block_minutes INTEGER NOT NULL,
            break_minutes INTEGER NOT NULL,
            max_daily_study_minutes INTEGER NOT NULL,
            study_location TEXT NOT NULL DEFAULT '',
            solo_preference_mode TEXT NOT NULL DEFAULT 'hard',
            group_preference_mode TEXT NOT NULL DEFAULT 'hard',
            commute_extra_buffer_minutes INTEGER NOT NULL DEFAULT 10,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    if old_rows:
        row = old_rows[0]
        conn.execute(
            """
            INSERT INTO user_preferences (
                user_id,
                preferred_start_hour,
                preferred_end_hour,
                block_minutes,
                break_minutes,
                max_daily_study_minutes,
                study_location,
                solo_preference_mode,
                group_preference_mode,
                commute_extra_buffer_minutes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                row["preferred_start_hour"],
                row["preferred_end_hour"],
                row["block_minutes"],
                row["break_minutes"],
                row["max_daily_study_minutes"],
                DEFAULT_PREFERENCES["study_location"],
                DEFAULT_PREFERENCES["solo_preference_mode"],
                DEFAULT_PREFERENCES["group_preference_mode"],
                DEFAULT_PREFERENCES["commute_extra_buffer_minutes"],
            ),
        )

    conn.execute("DROP TABLE user_preferences_old")


def migrate_calendar_events(conn):
    if not table_exists(conn, "calendar_events"):
        conn.execute(
            """
            CREATE TABLE calendar_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                google_event_id TEXT,
                title TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                location TEXT DEFAULT '',
                source TEXT NOT NULL DEFAULT 'google',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE (user_id, google_event_id)
            )
            """
        )
        return

    columns = get_columns(conn, "calendar_events")
    if "user_id" in columns and "source" in columns and "created_at" in columns:
        return

    old_columns = get_columns(conn, "calendar_events")
    select_google_id = "google_event_id" if "google_event_id" in old_columns else "NULL AS google_event_id"
    select_location = "location" if "location" in old_columns else "'' AS location"

    old_rows = conn.execute(
        f"""
        SELECT id, {select_google_id}, title, start_time, end_time, {select_location}
        FROM calendar_events
        """
    ).fetchall()

    conn.execute("ALTER TABLE calendar_events RENAME TO calendar_events_old")
    conn.execute(
        """
        CREATE TABLE calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            google_event_id TEXT,
            title TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            location TEXT DEFAULT '',
            source TEXT NOT NULL DEFAULT 'google',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE (user_id, google_event_id)
        )
        """
    )

    for row in old_rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO calendar_events (
                id,
                user_id,
                google_event_id,
                title,
                start_time,
                end_time,
                location,
                source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                1,
                row["google_event_id"],
                row["title"],
                row["start_time"],
                row["end_time"],
                row["location"] or "",
                "google",
            ),
        )

    conn.execute("DROP TABLE calendar_events_old")


def create_study_plans_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS study_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            total_hours INTEGER,
            deadline TEXT,
            study_location TEXT NOT NULL DEFAULT '',
            pushed_to_google INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )


def migrate_study_plans_columns(conn):
    columns = get_columns(conn, "study_plans")
    if "study_location" not in columns:
        conn.execute("ALTER TABLE study_plans ADD COLUMN study_location TEXT NOT NULL DEFAULT ''")


def create_study_blocks_table(conn):
    conn.execute(
        """
        CREATE TABLE study_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            location TEXT,
            duration_min INTEGER NOT NULL,
            is_done INTEGER NOT NULL DEFAULT 0,
            google_event_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (plan_id) REFERENCES study_plans(id) ON DELETE CASCADE,
            UNIQUE (user_id, plan_id, title, start_time, end_time)
        )
        """
    )




def ensure_default_preferences(conn, user_id: int):
    existing = conn.execute(
        "SELECT id FROM user_preferences WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    if existing:
        return

    conn.execute(
        """
        INSERT INTO user_preferences (
            user_id,
            preferred_start_hour,
            preferred_end_hour,
            block_minutes,
            break_minutes,
            max_daily_study_minutes,
            study_location,
            solo_preference_mode,
            group_preference_mode,
            commute_extra_buffer_minutes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            DEFAULT_PREFERENCES["preferred_start_hour"],
            DEFAULT_PREFERENCES["preferred_end_hour"],
            DEFAULT_PREFERENCES["block_minutes"],
            DEFAULT_PREFERENCES["break_minutes"],
            DEFAULT_PREFERENCES["max_daily_study_minutes"],
            DEFAULT_PREFERENCES["study_location"],
            DEFAULT_PREFERENCES["solo_preference_mode"],
            DEFAULT_PREFERENCES["group_preference_mode"],
            DEFAULT_PREFERENCES["commute_extra_buffer_minutes"],
        ),
    )


def user_exists(user_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    return row is not None


def create_user() -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO users (display_name) VALUES (?)",
            ("Nowy użytkownik",),
        )
        user_id = cursor.lastrowid
        conn.execute(
            "UPDATE users SET display_name = ? WHERE id = ?",
            (f"Użytkownik {user_id}", user_id),
        )
        ensure_default_preferences(conn, user_id)
        conn.commit()
        return int(user_id)


def get_user(user_id: int):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, display_name, google_sub, email, created_at, last_login_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
    return row_to_dict(row)


def get_or_create_google_user(
    google_sub: str | None,
    email: str | None,
    display_name: str | None,
    fallback_user_id: int | None = None,
) -> int:
    google_sub = normalize_optional_text(google_sub)
    email = normalize_optional_text(email)
    display_name = normalize_optional_text(display_name) or email or "Użytkownik Google"
    login_time = now_iso()

    with get_connection() as conn:
        row = None

        if google_sub:
            row = conn.execute(
                "SELECT id FROM users WHERE google_sub = ? LIMIT 1",
                (google_sub,),
            ).fetchone()

        if row is None and email:
            row = conn.execute(
                "SELECT id FROM users WHERE lower(email) = lower(?) LIMIT 1",
                (email,),
            ).fetchone()

        if row is not None:
            user_id = int(row["id"])
            conn.execute(
                """
                UPDATE users
                SET google_sub = COALESCE(?, google_sub),
                    email = COALESCE(?, email),
                    display_name = ?,
                    last_login_at = ?
                WHERE id = ?
                """,
                (google_sub, email, display_name, login_time, user_id),
            )
            ensure_default_preferences(conn, user_id)
            conn.commit()
            return user_id

        # Nie przypinamy już konta Google do wcześniej utworzonego lokalnego usera.
        # Stare lokalne rekordy mogą zostać wyczyszczone maintenance endpointem,
        # a nowe konta powstają wyłącznie jako realni użytkownicy Google.

        cursor = conn.execute(
            """
            INSERT INTO users (display_name, google_sub, email, last_login_at)
            VALUES (?, ?, ?, ?)
            """,
            (display_name, google_sub, email, login_time),
        )
        user_id = int(cursor.lastrowid)
        ensure_default_preferences(conn, user_id)
        conn.commit()
        return user_id


def list_users():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                u.id,
                u.display_name,
                u.email,
                u.google_sub,
                u.created_at,
                u.last_login_at,
                CASE WHEN c.user_id IS NULL THEN 0 ELSE 1 END AS has_google_credentials
            FROM users u
            LEFT JOIN google_credentials c ON c.user_id = u.id
            ORDER BY u.id
            """
        ).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        item["has_google_credentials"] = bool(item["has_google_credentials"])
        result.append(item)
    return result


def save_google_credentials(user_id: int, credentials: dict):
    token = credentials.get("token")
    if not token:
        raise ValueError("Brak tokenu Google do zapisania.")

    refresh_token = credentials.get("refresh_token")
    token_uri = credentials.get("token_uri") or "https://oauth2.googleapis.com/token"
    scopes = credentials.get("scopes") or ""
    expiry = credentials.get("expiry")

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT refresh_token FROM google_credentials WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if existing and not refresh_token:
            refresh_token = existing["refresh_token"]

        if existing:
            conn.execute(
                """
                UPDATE google_credentials
                SET token = ?,
                    refresh_token = ?,
                    token_uri = ?,
                    scopes = ?,
                    expiry = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (token, refresh_token, token_uri, scopes, expiry, user_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO google_credentials (user_id, token, refresh_token, token_uri, scopes, expiry)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, token, refresh_token, token_uri, scopes, expiry),
            )

        conn.commit()


def get_google_credentials(user_id: int):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT user_id, token, refresh_token, token_uri, scopes, expiry, updated_at
            FROM google_credentials
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

    return row_to_dict(row)


def has_google_credentials(user_id: int) -> bool:
    return get_google_credentials(user_id) is not None


def delete_google_credentials(user_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM google_credentials WHERE user_id = ?", (user_id,))
        conn.commit()


def clear_calendar_events(user_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM calendar_events WHERE user_id = ?", (user_id,))
        conn.commit()


def insert_imported_events(user_id: int, events: list[dict]):
    with get_connection() as conn:
        for event in events:
            conn.execute(
                """
                INSERT OR IGNORE INTO calendar_events (
                    user_id,
                    google_event_id,
                    title,
                    start_time,
                    end_time,
                    location,
                    source
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    event.get("id"),
                    event.get("summary") or "(bez nazwy)",
                    event.get("start") or "",
                    event.get("end") or "",
                    event.get("location") or "",
                    "google",
                ),
            )
        conn.commit()


def get_calendar_events(user_id: int):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, google_event_id, title, start_time, end_time, location, source, created_at
            FROM calendar_events
            WHERE user_id = ?
            ORDER BY start_time
            """,
            (user_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_calendar_events_between(user_id: int, start_date: str, end_date: str):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, google_event_id, title, start_time, end_time, location, source, created_at
            FROM calendar_events
            WHERE user_id = ?
              AND substr(start_time, 1, 10) >= ?
              AND substr(start_time, 1, 10) <= ?
            ORDER BY start_time
            """,
            (user_id, start_date, end_date),
        ).fetchall()

    return [dict(row) for row in rows]


def calendar_event_exists(user_id: int, title: str, start_time: str, end_time: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM calendar_events
            WHERE user_id = ? AND title = ? AND start_time = ? AND end_time = ?
            LIMIT 1
            """,
            (user_id, title, start_time, end_time),
        ).fetchone()

    return row is not None


def insert_calendar_event(user_id: int, event: dict, source: str = "google"):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO calendar_events (
                user_id,
                google_event_id,
                title,
                start_time,
                end_time,
                location,
                source
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                event.get("id"),
                event.get("summary") or "(bez nazwy)",
                event.get("start") or "",
                event.get("end") or "",
                event.get("location") or "",
                source,
            ),
        )
        conn.commit()


def get_user_preferences(user_id: int):
    with get_connection() as conn:
        ensure_default_preferences(conn, user_id)
        row = conn.execute(
            """
            SELECT
                preferred_start_hour,
                preferred_end_hour,
                block_minutes,
                break_minutes,
                max_daily_study_minutes,
                study_location,
                solo_preference_mode,
                group_preference_mode,
                commute_extra_buffer_minutes
            FROM user_preferences
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        conn.commit()

    return dict(row) if row else dict(DEFAULT_PREFERENCES)


def save_user_preferences(user_id: int, preferences: dict):
    study_location = str(preferences.get("study_location", "")).strip()
    solo_preference_mode = normalize_preference_mode(preferences.get("solo_preference_mode"))
    group_preference_mode = normalize_preference_mode(preferences.get("group_preference_mode"))
    commute_extra_buffer_minutes = int(
        preferences.get("commute_extra_buffer_minutes", DEFAULT_PREFERENCES["commute_extra_buffer_minutes"])
    )
    commute_extra_buffer_minutes = max(0, min(commute_extra_buffer_minutes, 120))

    with get_connection() as conn:
        ensure_default_preferences(conn, user_id)
        conn.execute(
            """
            UPDATE user_preferences
            SET
                preferred_start_hour = ?,
                preferred_end_hour = ?,
                block_minutes = ?,
                break_minutes = ?,
                max_daily_study_minutes = ?,
                study_location = ?,
                solo_preference_mode = ?,
                group_preference_mode = ?,
                commute_extra_buffer_minutes = ?
            WHERE user_id = ?
            """,
            (
                int(preferences["preferred_start_hour"]),
                int(preferences["preferred_end_hour"]),
                int(preferences["block_minutes"]),
                int(preferences["break_minutes"]),
                int(preferences["max_daily_study_minutes"]),
                study_location,
                solo_preference_mode,
                group_preference_mode,
                commute_extra_buffer_minutes,
                user_id,
            ),
        )
        conn.commit()

def set_study_plan_pushed(user_id: int, plan_id: int, pushed: bool = True):
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE study_plans
            SET pushed_to_google = ?
            WHERE id = ? AND user_id = ?
            """,
            (1 if pushed else 0, plan_id, user_id),
        )
        conn.commit()


def delete_calendar_event_by_google_id(user_id: int, google_event_id: str | None):
    if not google_event_id:
        return

    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM calendar_events
            WHERE user_id = ? AND google_event_id = ?
            """,
            (user_id, google_event_id),
        )
        conn.commit()


def get_study_stats(user_id: int, start_date: str, end_date: str, plan_id: int | None = None):
    blocks = get_study_blocks(user_id, start_date, end_date, plan_id)

    total_blocks = len(blocks)
    done_blocks = len([block for block in blocks if block["done"]])
    planned_minutes = sum(int(block["duration_min"]) for block in blocks)
    done_minutes = sum(int(block["duration_min"]) for block in blocks if block["done"])
    goal_percent = 0 if planned_minutes == 0 else round((done_minutes / planned_minutes) * 100)

    per_day_planned_minutes = {}
    per_day_done_minutes = {}
    per_week_done_minutes = {}
    done_hour_counter = {}

    for block in blocks:
        duration = int(block["duration_min"])
        day = block["date"]
        per_day_planned_minutes[day] = per_day_planned_minutes.get(day, 0) + duration

        if not block["done"]:
            continue

        per_day_done_minutes[day] = per_day_done_minutes.get(day, 0) + duration

        block_start = datetime.fromisoformat(block["start_time"])
        year, week, _ = block_start.isocalendar()
        week_key = f"{year}-W{week:02d}"
        per_week_done_minutes[week_key] = per_week_done_minutes.get(week_key, 0) + duration

        hour_key = block_start.strftime("%H:00")
        done_hour_counter[hour_key] = done_hour_counter.get(hour_key, 0) + 1

    best_day = None
    if per_day_done_minutes:
        best_day_key, best_day_minutes = max(per_day_done_minutes.items(), key=lambda item: item[1])
        best_day = {
            "date": best_day_key,
            "minutes": best_day_minutes,
        }

    most_common_hours = [
        {"hour": hour, "count": count}
        for hour, count in sorted(done_hour_counter.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]

    active_days_count = len(per_day_done_minutes)
    average_done_minutes_per_active_day = 0
    if active_days_count > 0:
        average_done_minutes_per_active_day = round(done_minutes / active_days_count)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "plan_id": plan_id,
        "total_blocks": total_blocks,
        "done_blocks": done_blocks,
        "planned_minutes": planned_minutes,
        "done_minutes": done_minutes,
        "remaining_minutes": max(0, planned_minutes - done_minutes),
        "goal_percent": goal_percent,
        "active_days_count": active_days_count,
        "average_done_minutes_per_active_day": average_done_minutes_per_active_day,
        "best_day": best_day,
        "most_common_hours": most_common_hours,
        "per_day_planned_minutes": dict(sorted(per_day_planned_minutes.items())),
        "per_day_done_minutes": dict(sorted(per_day_done_minutes.items())),
        "per_week_done_minutes": dict(sorted(per_week_done_minutes.items())),
    }



# --- MULTI USER GROUP PLANS PATCH ---

def create_study_plan_participants_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS study_plan_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'participant',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (plan_id) REFERENCES study_plans(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE (plan_id, user_id)
        )
        """
    )




def normalize_participant_user_ids(owner_user_id: int, participant_user_ids: list[int] | None):
    ids = [int(owner_user_id)]

    for raw_id in participant_user_ids or []:
        try:
            candidate = int(raw_id)
        except (TypeError, ValueError):
            continue

        if candidate not in ids and user_exists(candidate):
            ids.append(candidate)

    return ids


def set_study_plan_participants(plan_id: int, owner_user_id: int, participant_user_ids: list[int] | None):
    participant_ids = normalize_participant_user_ids(owner_user_id, participant_user_ids)

    with get_connection() as conn:
        create_study_plan_participants_table(conn)
        conn.execute("DELETE FROM study_plan_participants WHERE plan_id = ?", (plan_id,))

        for participant_id in participant_ids:
            role = "owner" if participant_id == int(owner_user_id) else "participant"
            conn.execute(
                """
                INSERT OR IGNORE INTO study_plan_participants (plan_id, user_id, role)
                VALUES (?, ?, ?)
                """,
                (plan_id, participant_id, role),
            )

        conn.commit()

    return participant_ids


def user_can_access_plan(user_id: int, plan_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM study_plan_participants
            WHERE plan_id = ? AND user_id = ?
            LIMIT 1
            """,
            (plan_id, user_id),
        ).fetchone()

    return row is not None


def get_plan_participants(plan_id: int):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                u.id,
                u.display_name,
                u.email,
                u.google_sub,
                spp.role
            FROM study_plan_participants spp
            JOIN users u ON u.id = spp.user_id
            WHERE spp.plan_id = ?
            ORDER BY CASE WHEN spp.role = 'owner' THEN 0 ELSE 1 END, lower(COALESCE(u.display_name, u.email, ''))
            """,
            (plan_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def list_available_participants(current_user_id: int | None = None):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                u.id,
                u.display_name,
                u.email,
                u.google_sub,
                CASE WHEN c.user_id IS NULL THEN 0 ELSE 1 END AS has_google_credentials,
                CASE WHEN u.id = ? THEN 1 ELSE 0 END AS is_current_user
            FROM users u
            LEFT JOIN google_credentials c ON c.user_id = u.id
            WHERE u.email IS NOT NULL
              AND trim(u.email) <> ''
              AND c.user_id IS NOT NULL
            ORDER BY is_current_user DESC, lower(COALESCE(u.display_name, u.email, ''))
            """,
            (current_user_id or -1,),
        ).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        item["has_google_credentials"] = bool(item["has_google_credentials"])
        item["is_current_user"] = bool(item["is_current_user"])
        result.append(item)
    return result




def create_study_plan(
    user_id: int,
    name: str,
    total_hours: int | None,
    deadline: str | None,
    participant_user_ids: list[int] | None = None,
    study_location: str | None = None,
) -> int:
    name = (name or "Nauka").strip() or "Nauka"
    study_location = (study_location or "").strip()
    participant_ids = normalize_participant_user_ids(user_id, participant_user_ids)

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO study_plans (user_id, name, total_hours, deadline, study_location, pushed_to_google)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, name, total_hours, deadline, study_location, 0),
        )
        plan_id = int(cursor.lastrowid)
        conn.commit()

    set_study_plan_participants(plan_id, user_id, participant_ids)
    return plan_id


def save_study_blocks(
    user_id: int,
    plan: list[tuple],
    block_name: str,
    total_hours: int | None = None,
    deadline_str: str | None = None,
    participant_user_ids: list[int] | None = None,
    study_location: str | None = None,
):
    block_name = (block_name or "Nauka").strip() or "Nauka"
    plan = sorted(plan, key=lambda item: item[0])
    plan_id = create_study_plan(
        user_id,
        block_name,
        total_hours,
        deadline_str,
        participant_user_ids,
        study_location=study_location,
    )

    with get_connection() as conn:
        for index, (start, end) in enumerate(plan, start=1):
            title = f"{block_name} blok {index}"
            start_iso = start.isoformat()
            end_iso = end.isoformat()
            duration_min = int((end - start).total_seconds() / 60)

            conn.execute(
                """
                INSERT INTO study_blocks (
                    user_id,
                    plan_id,
                    title,
                    start_time,
                    end_time,
                    location,
                    duration_min
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, plan_id, title, start_iso, end_iso, study_location,duration_min),
            )

        conn.commit()

    return {
        "plan": get_study_plan(user_id, plan_id),
        "blocks": get_study_blocks_by_plan(user_id, plan_id),
    }


def get_study_plan(user_id: int, plan_id: int):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                p.id,
                (
                    SELECT COUNT(*)
                    FROM study_plans p2
                    WHERE EXISTS (
                        SELECT 1
                        FROM study_plan_participants spp2
                        WHERE spp2.plan_id = p2.id AND spp2.user_id = ?
                    )
                      AND (
                          p2.created_at < p.created_at
                          OR (p2.created_at = p.created_at AND p2.id <= p.id)
                      )
                ) AS display_number,
                p.user_id,
                p.name,
                p.total_hours,
                p.deadline,
                p.study_location,
                p.pushed_to_google,
                p.created_at,
                COUNT(b.id) AS total_blocks,
                COALESCE(SUM(CASE WHEN b.is_done = 1 THEN 1 ELSE 0 END), 0) AS done_blocks,
                COALESCE(SUM(b.duration_min), 0) AS planned_minutes,
                COALESCE(SUM(CASE WHEN b.google_event_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS pushed_blocks
            FROM study_plans p
            LEFT JOIN study_blocks b ON b.plan_id = p.id
            WHERE p.id = ?
              AND EXISTS (
                  SELECT 1
                  FROM study_plan_participants spp
                  WHERE spp.plan_id = p.id AND spp.user_id = ?
              )
            GROUP BY p.id
            """,
            (user_id, plan_id, user_id),
        ).fetchone()

    return plan_row_to_api(row) if row else None


def get_study_plans(user_id: int):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                p.id,
                (
                    SELECT COUNT(*)
                    FROM study_plans p2
                    WHERE EXISTS (
                        SELECT 1
                        FROM study_plan_participants spp2
                        WHERE spp2.plan_id = p2.id AND spp2.user_id = ?
                    )
                      AND (
                          p2.created_at < p.created_at
                          OR (p2.created_at = p.created_at AND p2.id <= p.id)
                      )
                ) AS display_number,
                p.user_id,
                p.name,
                p.total_hours,
                p.deadline,
                p.study_location,
                p.pushed_to_google,
                p.created_at,
                COUNT(b.id) AS total_blocks,
                COALESCE(SUM(CASE WHEN b.is_done = 1 THEN 1 ELSE 0 END), 0) AS done_blocks,
                COALESCE(SUM(b.duration_min), 0) AS planned_minutes,
                COALESCE(SUM(CASE WHEN b.google_event_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS pushed_blocks
            FROM study_plans p
            LEFT JOIN study_blocks b ON b.plan_id = p.id
            WHERE EXISTS (
                SELECT 1
                FROM study_plan_participants spp
                WHERE spp.plan_id = p.id AND spp.user_id = ?
            )
            GROUP BY p.id
            ORDER BY p.created_at DESC, p.id DESC
            """,
            (user_id, user_id),
        ).fetchall()

    return [plan_row_to_api(row) for row in rows]


def get_study_blocks_by_plan(user_id: int, plan_id: int):
    return get_study_blocks(user_id, plan_id=plan_id)


def get_study_blocks(
    user_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
    plan_id: int | None = None,
):
    query = """
        SELECT b.id, b.user_id, b.plan_id, b.title, b.start_time, b.end_time, b.location, b.duration_min, b.is_done, b.google_event_id
        FROM study_blocks b
        JOIN study_plans p ON p.id = b.plan_id
        WHERE EXISTS (
            SELECT 1
            FROM study_plan_participants spp
            WHERE spp.plan_id = p.id AND spp.user_id = ?
        )
    """
    params: list = [user_id]

    if plan_id is not None:
        query += " AND p.id = ?"
        params.append(plan_id)

    if start_date:
        query += " AND b.start_time >= ?"
        params.append(f"{start_date}T00:00:00")

    if end_date:
        end_exclusive = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        query += " AND b.start_time < ?"
        params.append(end_exclusive.strftime("%Y-%m-%dT00:00:00"))

    query += " ORDER BY b.start_time, b.id"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [block_row_to_api(row) for row in rows]


def get_study_block(user_id: int, block_id: int):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT b.id, b.user_id, b.plan_id, b.title, b.start_time, b.end_time, b.location, b.duration_min, b.is_done, b.google_event_id
            FROM study_blocks b
            JOIN study_plans p ON p.id = b.plan_id
            WHERE b.id = ?
              AND EXISTS (
                  SELECT 1
                  FROM study_plan_participants spp
                  WHERE spp.plan_id = p.id AND spp.user_id = ?
              )
            """,
            (block_id, user_id),
        ).fetchone()

    return block_row_to_api(row) if row else None


def set_study_block_done(user_id: int, block_id: int, done: bool):
    block = get_study_block(user_id, block_id)
    if not block:
        return None

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE study_blocks
            SET is_done = ?
            WHERE id = ?
            """,
            (1 if done else 0, block_id),
        )
        conn.commit()

    return get_study_block(user_id, block_id)


def update_study_block_google_event_id(user_id: int, block_id: int, google_event_id: str):
    block = get_study_block(user_id, block_id)
    if not block:
        return

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE study_blocks
            SET google_event_id = ?
            WHERE id = ?
            """,
            (google_event_id, block_id),
        )
        conn.commit()


def clear_study_block_google_event_id(user_id: int, block_id: int):
    block = get_study_block(user_id, block_id)
    if not block:
        return

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE study_blocks
            SET google_event_id = NULL
            WHERE id = ?
            """,
            (block_id,),
        )
        conn.commit()


def get_calendar_events_for_users(user_ids: list[int]):
    if not user_ids:
        return []

    placeholders = ",".join(["?"] * len(user_ids))

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, user_id, google_event_id, title, start_time, end_time, location, source, created_at
            FROM calendar_events
            WHERE user_id IN ({placeholders})
            ORDER BY start_time
            """,
            [int(user_id) for user_id in user_ids],
        ).fetchall()

    return [dict(row) for row in rows]


# --- LOCAL DELETE / CLEANUP / GROUP GOOGLE EVENTS PATCH ---

def create_study_block_google_events_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS study_block_google_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            google_event_id TEXT NOT NULL,
            pushed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (block_id) REFERENCES study_blocks(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE (block_id, user_id)
        )
        """
    )


def migrate_existing_block_google_events(conn):
    create_study_block_google_events_table(conn)

    columns = get_columns(conn, "study_blocks")
    if "google_event_id" not in columns:
        return

    conn.execute(
        """
        INSERT OR IGNORE INTO study_block_google_events (block_id, user_id, google_event_id)
        SELECT id, user_id, google_event_id
        FROM study_blocks
        WHERE google_event_id IS NOT NULL
          AND trim(google_event_id) <> ''
        """
    )
def migrate_study_blocks_columns(conn):
    columns = get_columns(conn, "study_blocks")

    if "location" not in columns:
        conn.execute(
            "ALTER TABLE study_blocks ADD COLUMN location TEXT DEFAULT ''"
        )

def migrate_study_plans_and_blocks(conn):
    create_study_plans_table(conn)
    migrate_study_plans_columns(conn)

    if not table_exists(conn, "study_blocks"):
        create_study_blocks_table(conn)
    else:
        columns = get_columns(conn, "study_blocks")
        migrate_study_blocks_columns(conn)
        if "plan_id" not in columns:
            old_rows = conn.execute(
                """
                SELECT id, user_id, title, start_time, end_time, duration_min, is_done, google_event_id, created_at
                FROM study_blocks
                ORDER BY user_id, start_time, id
                """
            ).fetchall()

            conn.execute("ALTER TABLE study_blocks RENAME TO study_blocks_old")
            create_study_blocks_table(conn)

            if old_rows:
                plan_ids = {}

                for row in old_rows:
                    user_id = row["user_id"] if "user_id" in row.keys() else 1

                    if user_id not in plan_ids:
                        cursor = conn.execute(
                            """
                            INSERT INTO study_plans (user_id, name, total_hours, deadline, study_location, pushed_to_google)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (user_id, "Stare bloki nauki", None, None, "", 0),
                        )
                        plan_ids[user_id] = cursor.lastrowid

                    conn.execute(
                        """
                        INSERT OR IGNORE INTO study_blocks (
                            id,
                            user_id,
                            plan_id,
                            title,
                            start_time,
                            end_time,
                            duration_min,
                            is_done,
                            google_event_id,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["id"],
                            user_id,
                            plan_ids[user_id],
                            row["title"],
                            row["start_time"],
                            row["end_time"],
                            row["duration_min"],
                            row["is_done"],
                            row["google_event_id"],
                            row["created_at"],
                        ),
                    )

            conn.execute("DROP TABLE study_blocks_old")

    create_study_plan_participants_table(conn)
    create_study_block_google_events_table(conn)
    migrate_existing_block_google_events(conn)

    conn.execute(
        """
        INSERT OR IGNORE INTO study_plan_participants (plan_id, user_id, role)
        SELECT id, user_id, 'owner'
        FROM study_plans
        """
    )


def get_block_google_events(block_id: int):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                sbge.block_id,
                sbge.user_id,
                sbge.google_event_id,
                sbge.pushed_at,
                u.display_name,
                u.email
            FROM study_block_google_events sbge
            JOIN users u ON u.id = sbge.user_id
            WHERE sbge.block_id = ?
            ORDER BY lower(COALESCE(u.display_name, u.email, ''))
            """,
            (block_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def block_row_to_api(row):
    start = datetime.fromisoformat(row["start_time"])
    end = datetime.fromisoformat(row["end_time"])
    block_id = int(row["id"])
    google_events = get_block_google_events(block_id)

    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "plan_id": row["plan_id"],
        "title": row["title"],
        "date": start.strftime("%Y-%m-%d"),
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "location": row["location"] or "",
        "duration_min": row["duration_min"],
        "done": bool(row["is_done"]),
        "google_event_id": row["google_event_id"],
        "google_events": google_events,
        "pushed_to_google_count": len(google_events),
    }


def get_plan_google_push_info(plan_id: int):
    with get_connection() as conn:
        blocks_count_row = conn.execute(
            "SELECT COUNT(*) AS count FROM study_blocks WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        participants_count_row = conn.execute(
            "SELECT COUNT(*) AS count FROM study_plan_participants WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        pushed_count_row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM study_block_google_events sbge
            JOIN study_blocks b ON b.id = sbge.block_id
            WHERE b.plan_id = ?
            """,
            (plan_id,),
        ).fetchone()

    blocks_count = int(blocks_count_row["count"] or 0)
    participants_count = int(participants_count_row["count"] or 0)
    expected_count = blocks_count * max(1, participants_count)
    pushed_count = int(pushed_count_row["count"] or 0)

    return {
        "blocks_count": blocks_count,
        "participants_count": participants_count,
        "expected_google_events": expected_count,
        "pushed_google_events": pushed_count,
        "fully_pushed": expected_count > 0 and pushed_count >= expected_count,
    }


def plan_row_to_api(row):
    total_blocks = int(row["total_blocks"] or 0)
    done_blocks = int(row["done_blocks"] or 0)
    planned_minutes = int(row["planned_minutes"] or 0)
    display_number = row["display_number"] if "display_number" in row.keys() else row["id"]
    participants = get_plan_participants(int(row["id"]))
    participants_count = len(participants)
    owner = next((participant for participant in participants if participant.get("role") == "owner"), None)
    push_info = get_plan_google_push_info(int(row["id"]))

    return {
        "id": row["id"],
        "display_number": int(display_number or row["id"]),
        "user_id": row["user_id"],
        "owner_user_id": row["user_id"],
        "owner_display_name": owner.get("display_name") if owner else None,
        "name": row["name"],
        "total_hours": row["total_hours"],
        "deadline": row["deadline"],
        "study_location": row["study_location"] if "study_location" in row.keys() else "",
        "pushed_to_google": bool(row["pushed_to_google"]) or bool(push_info["fully_pushed"]),
        "created_at": row["created_at"],
        "total_blocks": total_blocks,
        "done_blocks": done_blocks,
        "planned_minutes": planned_minutes,
        "pushed_blocks": push_info["pushed_google_events"],
        "expected_google_events": push_info["expected_google_events"],
        "pushed_google_events": push_info["pushed_google_events"],
        "participants": participants,
        "participants_count": participants_count,
        "is_group": participants_count > 1,
    }


def upsert_study_block_google_event(block_id: int, user_id: int, google_event_id: str):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO study_block_google_events (block_id, user_id, google_event_id, pushed_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(block_id, user_id) DO UPDATE SET
                google_event_id = excluded.google_event_id,
                pushed_at = CURRENT_TIMESTAMP
            """,
            (block_id, user_id, google_event_id),
        )
        conn.commit()


def get_study_block_google_event(block_id: int, user_id: int):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT block_id, user_id, google_event_id, pushed_at
            FROM study_block_google_events
            WHERE block_id = ? AND user_id = ?
            """,
            (block_id, user_id),
        ).fetchone()
    return row_to_dict(row)


def clear_study_block_google_event_for_user(block_id: int, user_id: int):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM study_block_google_events WHERE block_id = ? AND user_id = ?",
            (block_id, user_id),
        )
        owner_row = conn.execute(
            "SELECT user_id FROM study_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()
        if owner_row and int(owner_row["user_id"]) == int(user_id):
            conn.execute("UPDATE study_blocks SET google_event_id = NULL WHERE id = ?", (block_id,))
        conn.commit()


def get_study_block_google_events_by_plan(plan_id: int):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                sbge.block_id,
                sbge.user_id,
                sbge.google_event_id,
                sbge.pushed_at,
                b.title,
                b.start_time,
                b.end_time,
                u.display_name,
                u.email
            FROM study_block_google_events sbge
            JOIN study_blocks b ON b.id = sbge.block_id
            JOIN users u ON u.id = sbge.user_id
            WHERE b.plan_id = ?
            ORDER BY b.start_time, b.id, lower(COALESCE(u.display_name, u.email, ''))
            """,
            (plan_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def refresh_study_plan_google_status(user_id: int, plan_id: int):
    if not user_can_access_plan(user_id, plan_id):
        return None

    push_info = get_plan_google_push_info(plan_id)
    set_study_plan_pushed(user_id, plan_id, push_info["fully_pushed"])
    return get_study_plan(user_id, plan_id)


def delete_study_plan_locally(user_id: int, plan_id: int):
    plan = get_study_plan(user_id, plan_id)
    if not plan:
        return None

    with get_connection() as conn:
        conn.execute("DELETE FROM study_block_google_events WHERE block_id IN (SELECT id FROM study_blocks WHERE plan_id = ?)", (plan_id,))
        conn.execute("DELETE FROM study_blocks WHERE plan_id = ?", (plan_id,))
        conn.execute("DELETE FROM study_plan_participants WHERE plan_id = ?", (plan_id,))
        conn.execute("DELETE FROM study_plans WHERE id = ?", (plan_id,))
        if "sqlite_sequence" in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
            for table in ("study_block_google_events", "study_blocks", "study_plan_participants", "study_plans"):
                conn.execute("DELETE FROM sqlite_sequence WHERE name = ? AND NOT EXISTS (SELECT 1 FROM " + table + ")", (table,))
        conn.commit()

    return plan


def cleanup_local_users():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT u.id
            FROM users u
            LEFT JOIN google_credentials c ON c.user_id = u.id
            WHERE c.user_id IS NULL
              AND (u.email IS NULL OR trim(u.email) = '')
              AND (u.google_sub IS NULL OR trim(u.google_sub) = '')
            """
        ).fetchall()

        user_ids = [int(row["id"]) for row in rows]
        if not user_ids:
            return {"deleted_users": 0, "deleted_user_ids": []}

        placeholders = ",".join(["?"] * len(user_ids))
        owned_plan_rows = conn.execute(
            f"SELECT id FROM study_plans WHERE user_id IN ({placeholders})",
            user_ids,
        ).fetchall()
        owned_plan_ids = [int(row["id"]) for row in owned_plan_rows]

        if owned_plan_ids:
            plan_placeholders = ",".join(["?"] * len(owned_plan_ids))
            conn.execute(
                f"DELETE FROM study_block_google_events WHERE block_id IN (SELECT id FROM study_blocks WHERE plan_id IN ({plan_placeholders}))",
                owned_plan_ids,
            )
            conn.execute(f"DELETE FROM study_blocks WHERE plan_id IN ({plan_placeholders})", owned_plan_ids)
            conn.execute(f"DELETE FROM study_plan_participants WHERE plan_id IN ({plan_placeholders})", owned_plan_ids)
            conn.execute(f"DELETE FROM study_plans WHERE id IN ({plan_placeholders})", owned_plan_ids)

        conn.execute(f"DELETE FROM study_plan_participants WHERE user_id IN ({placeholders})", user_ids)
        conn.execute(f"DELETE FROM calendar_events WHERE user_id IN ({placeholders})", user_ids)
        conn.execute(f"DELETE FROM user_preferences WHERE user_id IN ({placeholders})", user_ids)
        conn.execute(f"DELETE FROM google_credentials WHERE user_id IN ({placeholders})", user_ids)
        conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)
        conn.commit()

    return {"deleted_users": len(user_ids), "deleted_user_ids": user_ids}


def create_notification(user_id: int, notification_type: str, title: str, message: str, plan_id: int | None = None):
    """Tworzy powiadomienie dla użytkownika."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO notifications (user_id, type, title, message, plan_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, notification_type, title, message, plan_id),
        )
        conn.commit()


def get_user_notifications(user_id: int, unread_only: bool = False):
    """Pobiera powiadomienia użytkownika."""
    with get_connection() as conn:
        query = """
            SELECT id, user_id, type, title, message, plan_id, read, created_at
            FROM notifications
            WHERE user_id = ?
        """
        if unread_only:
            query += " AND read = 0"
        query += " ORDER BY created_at DESC"

        rows = conn.execute(query, (user_id,)).fetchall()
        return [dict(row) for row in rows]


def mark_notification_as_read(user_id: int, notification_id: int):
    """Oznacza powiadomienie jako przeczytane."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE notifications
            SET read = 1
            WHERE id = ? AND user_id = ?
            """,
            (notification_id, user_id),
        )
        conn.commit()


def mark_all_notifications_as_read(user_id: int):
    """Oznacza wszystkie powiadomienia użytkownika jako przeczytane."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE notifications
            SET read = 1
            WHERE user_id = ?
            """,
            (user_id,),
        )
        conn.commit()


def notify_participants_about_new_plan(plan_id: int, owner_user_id: int, plan_title: str):
    """Tworzy powiadomienia dla wszystkich uczestników nowego planu (oprócz właściciela)."""
    try:
        participants = get_plan_participants(plan_id)
        print(f"[NOTIFY] Plan {plan_id} ma {len(participants)} uczestników")

        owner = get_user(owner_user_id)
        owner_name = owner.get("display_name") or owner.get("email") or f"Użytkownik {owner_user_id}"

        notification_count = 0
        for participant in participants:
            participant_id = int(participant["id"])
            if participant_id != owner_user_id:
                create_notification(
                    user_id=participant_id,
                    notification_type="new_plan",
                    title="Nowy plan nauki",
                    message=f"{owner_name} dodał Cię do planu: {plan_title}",
                    plan_id=plan_id,
                )
                notification_count += 1
                print(f"[NOTIFY] ✓ Utworzono powiadomienie dla user_id={participant_id}")

        print(f"[NOTIFY] ✓ Utworzono {notification_count} powiadomień dla planu {plan_id}")
    except Exception as e:
        print(f"[NOTIFY] ✗ Błąd: {e}")
        import traceback
        traceback.print_exc()
        raise


def auto_sync_calendar_after_push(user_id: int, plan_id: int):
    """Automatycznie synchronizuje bazę danych po wysłaniu planu do Google Calendar."""
    try:
        from Google_calendar_api import get_events

        # Pobierz świeże wydarzenia z Google Calendar
        events = get_events(user_id)

        # Zaktualizuj bazę danych (dodaj nowe wydarzenia)
        insert_imported_events(user_id, events)

        # Odśwież status planu
        refresh_study_plan_google_status(user_id, plan_id)

        print(f"[AUTO-SYNC] ✓ Zaktualizowano bazę dla user_id={user_id}, plan_id={plan_id}")
        return {"success": True, "message": "Baza została automatycznie zaktualizowana"}
    except Exception as e:
        print(f"[AUTO-SYNC] ✗ Błąd dla user_id={user_id}: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "message": f"Błąd automatycznej synchronizacji: {e}"}
