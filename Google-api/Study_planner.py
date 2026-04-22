from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from Google_calendar_api import add_event
from db import (
    get_calendar_events,
    calendar_event_exists,
    insert_calendar_event,
    get_user_preferences,
)

TIMEZONE = ZoneInfo("Europe/Warsaw")


def parse_datetime(value: str):
    if not value:
        raise ValueError("Pusty czas wydarzenia.")

    value = value.strip()

    if "T" in value:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TIMEZONE)
        return dt.astimezone(TIMEZONE)

    return datetime.fromisoformat(value).replace(tzinfo=TIMEZONE)


def get_busy_slots_from_saved_events(events):
    busy_slots = []

    for event in events:
        start_data = event.get("start_time")
        end_data = event.get("end_time")

        if start_data and end_data:
            start_dt = parse_datetime(start_data)
            end_dt = parse_datetime(end_data)
            busy_slots.append((start_dt, end_dt))

    return busy_slots


def overlaps(start1, end1, start2, end2):
    return start1 < end2 and start2 < end1


def is_free(candidate_start, candidate_end, busy_slots):
    for busy_start, busy_end in busy_slots:
        if overlaps(candidate_start, candidate_end, busy_start, busy_end):
            return False
    return True


def generate_study_plan_from_saved_events(total_hours, deadline_str):
    preferences = get_user_preferences()

    preferred_start_hour = int(preferences["preferred_start_hour"])
    preferred_end_hour = int(preferences["preferred_end_hour"])
    block_minutes = int(preferences["block_minutes"])
    break_minutes = int(preferences["break_minutes"])
    max_daily_study_minutes = int(preferences["max_daily_study_minutes"])

    if not deadline_str:
        raise ValueError("Brak deadline.")

    now = datetime.now(TIMEZONE)
    deadline = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)

    if deadline <= now:
        raise ValueError("Deadline musi być w przyszłości.")

    if preferred_start_hour >= preferred_end_hour:
        raise ValueError("Godzina początku musi być mniejsza niż godzina końca.")

    saved_events = get_calendar_events()
    busy_slots = get_busy_slots_from_saved_events(saved_events)

    study_blocks = []
    remaining_minutes = int(total_hours) * 60
    current_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    while current_day <= deadline and remaining_minutes > 0:
        day_start = current_day.replace(hour=preferred_start_hour, minute=0)
        day_end = current_day.replace(hour=preferred_end_hour, minute=0)

        if current_day.date() == now.date() and day_start < now:
            day_start = now + timedelta(minutes=5)

        daily_planned = 0
        current_time = day_start

        while (
            current_time < day_end
            and remaining_minutes > 0
            and daily_planned < max_daily_study_minutes
        ):
            current_block_minutes = min(
                block_minutes,
                remaining_minutes,
                max_daily_study_minutes - daily_planned,
            )

            candidate_start = current_time
            candidate_end = candidate_start + timedelta(minutes=current_block_minutes)

            if candidate_end > day_end:
                break

            if candidate_end > deadline:
                break

            if is_free(candidate_start, candidate_end, busy_slots):
                study_blocks.append((candidate_start, candidate_end))
                remaining_minutes -= current_block_minutes
                daily_planned += current_block_minutes
                current_time = candidate_end + timedelta(minutes=break_minutes)
            else:
                current_time += timedelta(minutes=15)

        current_day += timedelta(days=1)

    return study_blocks


def format_study_plan(study_blocks):
    formatted = []
    for start, end in study_blocks:
        formatted.append(
            {
                "date": start.strftime("%Y-%m-%d"),
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "duration_min": int((end - start).total_seconds() / 60),
            }
        )
    return formatted


def save_plan_to_google(plan):
    pushed = []
    skipped = []

    for index, (start, end) in enumerate(plan, start=1):
        title = f"Nauka blok {index}"
        start_iso = start.isoformat()
        end_iso = end.isoformat()

        if calendar_event_exists(title, start_iso, end_iso):
            skipped.append(
                {
                    "title": title,
                    "start_time": start_iso,
                    "end_time": end_iso,
                }
            )
            continue

        new_event = add_event(
            start_iso,
            end_iso,
            title,
            "Wroclaw",
        )

        insert_calendar_event(new_event)
        pushed.append(new_event)

    return {
        "pushed": pushed,
        "skipped": skipped,
    }