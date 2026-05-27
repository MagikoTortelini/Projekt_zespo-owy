from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo
from Map_distance_API import Distance_and_tiem
from Google_calendar_api import add_event, delete_google_event, google_event_exists
from db import get_user_preferences

TIMEZONE = ZoneInfo("Europe/Warsaw")
SEARCH_STEP_MINUTES = 15
DEFAULT_AFTER_CALENDAR_EVENT_BREAK_MINUTES = 15
DEFAULT_BEFORE_CALENDAR_EVENT_BREAK_MINUTES = 0


def parse_datetime(value: str) -> datetime:
    if not value:
        raise ValueError("Pusty czas wydarzenia.")

    value = value.strip()

    if "T" in value:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TIMEZONE)
        return dt.astimezone(TIMEZONE)

    return datetime.fromisoformat(value).replace(tzinfo=TIMEZONE)


def round_up_datetime(dt: datetime, step_minutes: int = SEARCH_STEP_MINUTES) -> datetime:
    """Zaokrągla czas do najbliższego kwadransa, żeby nie tworzyć bloków np. 17:38-19:08."""
    if step_minutes <= 0:
        return dt.replace(second=0, microsecond=0)

    dt = dt.replace(second=0, microsecond=0)
    minute_rest = dt.minute % step_minutes

    if minute_rest == 0:
        return dt

    return dt + timedelta(minutes=step_minutes - minute_rest)


def overlaps(start1: datetime, end1: datetime, start2: datetime, end2: datetime) -> bool:
    return start1 < end2 and start2 < end1

@lru_cache(maxsize=128)
def get_cached_distance_and_time(loc1: str, loc2: str):
    #Zwraca dystans i czas, ale zapamiętuje wyniki, żeby nie liczyć dwa razy tego samego.
    return Distance_and_tiem(loc1, loc2)
def is_free(
    owner_user_id,
    candidate_start: datetime,
    candidate_end: datetime,
    candidate_location: str,
    busy_slots
) -> bool:
    # Preferencje użytkownika do czasu przerwy po dojeździe oraz symlowana prędkość samchodu
    preferences = get_user_preferences(owner_user_id)
    EXTRA_BREAK_MINUTES = int(preferences["commute_extra_buffer_minutes"])
    CAR_SPEED_M_S=8.3

    for slot in busy_slots:

        slot_start = slot["start"]
        slot_end = slot["end"]
        slot_location = slot.get("location", "")

        # overlap
        if overlaps(candidate_start, candidate_end, slot_start, slot_end):
            return False

        # jeśli brak lokalizacji pomijamy travel
        if not slot_location or not candidate_location:
            continue

        # Dodjazd z wydarzenia z kalędarza na miejsce nauki
        if slot_end <= candidate_start:
            # Obliczenie dystansu oraz czasu pieszego
            distance, travel_seconds = get_cached_distance_and_time(
                slot_location,
                candidate_location
            )
            # symulacja podróży autem gdy odległość jest większa niż 2km
            if distance>2000:
                travel_seconds=int((distance/CAR_SPEED_M_S))
            # czas dotarcia plus przerwa gdy wydarzenie jest dalej niż 200m
            if(distance>200):
                arrival_time = (
                    slot_end
                    + timedelta(seconds=travel_seconds)
                    + timedelta(minutes=EXTRA_BREAK_MINUTES)
                )
            # gdy odlesgłośc jest mniejsza niż 200m nie dodajemy przerwy
            else:
                arrival_time = (
                        slot_end
                        + timedelta(seconds=travel_seconds)
                        + timedelta(minutes=0)
                )

            # Sprawdzenie czy zdążymy
            if arrival_time > candidate_start:
                return False

        # Dojazd z miejsca nauki na następne wydarzenie
        if candidate_end <= slot_start:
            # Obliczenie dystansu oraz czasu pieszego
            distance, travel_seconds = get_cached_distance_and_time(
                candidate_location,
                slot_location
            )
            # symulacja podróży autem gdy odległość jest większa niż 2km
            if distance>2000:
                travel_seconds=int((distance/CAR_SPEED_M_S))
            # czas dotarcia plus przerwa gdy wydarzenie jest dalej niż 200m
            if (distance > 200):
                leave_time = (
                    candidate_end
                    + timedelta(seconds=travel_seconds)
                    + timedelta(minutes=EXTRA_BREAK_MINUTES)
                )
            # gdy odlesgłośc jest mniejsza niż 200m nie dodajemy przerwy
            else:
                leave_time = (
                        candidate_end
                        + timedelta(seconds=travel_seconds)
                        + timedelta(minutes=0)
                )

            if leave_time > slot_start:
                return False

    return True
def get_busy_slots_from_saved_events(
    events: list[dict],
    location,
    add_location_buffer: bool = True,
    participant_user_ids: list[int] | None = None,
) -> list[tuple[datetime, datetime]]:
    busy_slots = []

    for event in events:
        start_data = event.get("start_time")
        end_data = event.get("end_time")

        if not start_data or not end_data:
            continue

        start_dt = parse_datetime(start_data)
        end_dt = parse_datetime(end_data)


        busy_slots.append({
            "start": start_dt,
            "end": end_dt,
            "location": event.get("location", "")
        })

    return busy_slots


def get_busy_slots_from_saved_blocks(blocks: list[dict]) -> list[tuple[datetime, datetime]]:
    busy_slots = []

    for block in blocks:
        start_data = block.get("start_time")
        end_data = block.get("end_time")

        if not start_data or not end_data:
            continue

        busy_slots.append({
            "start": parse_datetime(start_data),
            "end": parse_datetime(end_data),
            "location": block.get("location", "")
        })

    return busy_slots


def generate_study_plan_for_users(
    owner_user_id: int,
    participant_user_ids: list[int],
    total_hours,
    deadline_str,
    location
) -> list[tuple[datetime, datetime]]:
    from db import get_calendar_events_for_users, get_study_blocks, normalize_participant_user_ids

    participant_ids = normalize_participant_user_ids(owner_user_id, participant_user_ids)
    preferences = get_user_preferences(owner_user_id)

    preferred_start_hour = int(preferences["preferred_start_hour"])
    preferred_end_hour = int(preferences["preferred_end_hour"])
    block_minutes = int(preferences["block_minutes"])
    break_minutes = int(preferences["break_minutes"])
    max_daily_study_minutes = int(preferences["max_daily_study_minutes"])

    total_hours_int = int(total_hours)
    if total_hours_int <= 0:
        raise ValueError("Liczba godzin nauki musi być większa od zera.")

    if not deadline_str:
        raise ValueError("Brak deadline.")

    now = datetime.now(TIMEZONE)
    deadline = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)

    if deadline <= now:
        raise ValueError("Deadline musi być w przyszłości.")

    if preferred_start_hour >= preferred_end_hour:
        raise ValueError("Godzina początku musi być mniejsza niż godzina końca.")

    saved_events = get_calendar_events_for_users(participant_ids)
    busy_slots = get_busy_slots_from_saved_events(
        saved_events,
        location,
        add_location_buffer=True,
        participant_user_ids=participant_ids,
    )

    # Istniejące bloki uczestników traktujemy jako zajęty czas, ale bez bufora lokalizacyjnego.
    for participant_id in participant_ids:
        existing_blocks = get_study_blocks(participant_id)
        busy_slots.extend(get_busy_slots_from_saved_blocks(existing_blocks))

    study_blocks = []
    remaining_minutes = total_hours_int * 60
    current_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    while current_day <= deadline and remaining_minutes > 0:
        day_start = current_day.replace(hour=preferred_start_hour, minute=0)
        day_end = current_day.replace(hour=preferred_end_hour, minute=0)

        if current_day.date() == now.date() and day_start < now:
            day_start = round_up_datetime(now + timedelta(minutes=5))

        daily_planned = 0
        current_time = day_start

        while current_time < day_end and remaining_minutes > 0 and daily_planned < max_daily_study_minutes:
            current_block_minutes = min(
                block_minutes,
                remaining_minutes,
                max_daily_study_minutes - daily_planned,
            )

            candidate_start = current_time
            candidate_end = candidate_start + timedelta(minutes=current_block_minutes)

            # Na razie nie wciskamy krótszych bloków na końcówkę dnia.
            # Jeśli nie ma miejsca na cały aktualny blok, przechodzimy do następnego dnia.
            if candidate_end > day_end:
                break

            if candidate_end > deadline:
                break

            if is_free(owner_user_id,candidate_start,candidate_end,location,busy_slots):
                study_blocks.append((candidate_start, candidate_end))
                busy_slots.append({
                    "start": candidate_start,
                    "end": candidate_end,
                    "location": location
                })
                remaining_minutes -= current_block_minutes
                daily_planned += current_block_minutes
                current_time = candidate_end + timedelta(minutes=break_minutes)
            else:
                current_time += timedelta(minutes=SEARCH_STEP_MINUTES)

        current_day += timedelta(days=1)

    if remaining_minutes > 0:
        planned_hours = (total_hours_int * 60 - remaining_minutes) / 60
        raise ValueError(
            f"Nie udało się zaplanować całego czasu dla wszystkich uczestników. "
            f"Zaplanowano {planned_hours:.2f}h z {total_hours_int}h. "
            "Spróbuj wydłużyć deadline albo zakres godzin nauki."
        )

    return study_blocks


def generate_study_plan_from_saved_events(user_id: int, total_hours, deadline_str):
    return generate_study_plan_for_users(user_id, [user_id], total_hours, deadline_str)


def save_blocks_to_google(user_id: int, blocks: list[dict]):
    from db import (
        calendar_event_exists,
        clear_study_block_google_event_for_user,
        delete_calendar_event_by_google_id,
        get_google_credentials,
        get_plan_participants,
        get_study_plan,
        get_study_block,
        get_study_block_google_event,
        insert_calendar_event,
        refresh_study_plan_google_status,
        update_study_block_google_event_id,
        upsert_study_block_google_event,
    )

    pushed = []
    skipped = []

    if not blocks:
        return {"pushed": pushed, "skipped": skipped}

    plan_id = int(blocks[0]["plan_id"])
    plan = get_study_plan(user_id, plan_id)
    plan_location = ((plan.get("study_location") if plan else "") or "").strip()
    participants = get_plan_participants(plan_id)
    if not participants:
        participants = [{"id": int(user_id), "display_name": "Aktualny użytkownik", "email": None}]

    for block in blocks:
        block_id = int(block["id"])
        fresh_block = get_study_block(user_id, block_id)

        if not fresh_block:
            continue

        title = fresh_block["title"]
        start_iso = fresh_block["start_time"]
        end_iso = fresh_block["end_time"]

        for participant in participants:
            participant_id = int(participant["id"])
            participant_label = participant.get("email") or participant.get("display_name") or f"user {participant_id}"

            credentials = get_google_credentials(participant_id)
            if not credentials:
                skipped.append(
                    {
                        "title": title,
                        "start_time": start_iso,
                        "end_time": end_iso,
                        "user_id": participant_id,
                        "user_label": participant_label,
                        "reason": "Uczestnik nie ma aktywnego logowania Google.",
                    }
                )
                continue

            existing_link = get_study_block_google_event(block_id, participant_id)
            if existing_link and existing_link.get("google_event_id"):
                existing_event_id = existing_link["google_event_id"]
                if google_event_exists(participant_id, existing_event_id):
                    skipped.append(
                        {
                            "title": title,
                            "start_time": start_iso,
                            "end_time": end_iso,
                            "user_id": participant_id,
                            "user_label": participant_label,
                            "reason": "Ten blok był już wysłany do kalendarza tego uczestnika.",
                        }
                    )
                    continue

                clear_study_block_google_event_for_user(block_id, participant_id)
                delete_calendar_event_by_google_id(participant_id, existing_event_id)

            if calendar_event_exists(participant_id, title, start_iso, end_iso):
                skipped.append(
                    {
                        "title": title,
                        "start_time": start_iso,
                        "end_time": end_iso,
                        "user_id": participant_id,
                        "user_label": participant_label,
                        "reason": "Podobne wydarzenie istnieje już w bazie tego uczestnika.",
                    }
                )
                continue

            new_event = add_event(participant_id, start_iso, end_iso, title, plan_location)

            insert_calendar_event(participant_id, new_event, source="study_block")
            upsert_study_block_google_event(block_id, participant_id, new_event.get("id"))

            # Pole w study_blocks zostaje jako kompatybilność dla starych planów solo.
            if participant_id == int(fresh_block["user_id"]):
                update_study_block_google_event_id(user_id, block_id, new_event.get("id"))

            pushed.append(
                {
                    "event": new_event,
                    "user_id": participant_id,
                    "user_label": participant_label,
                    "block_id": block_id,
                }
            )

    refresh_study_plan_google_status(user_id, plan_id)
    return {"pushed": pushed, "skipped": skipped}


def sync_plan_google_state(user_id: int, plan_id: int):
    from db import (
        clear_study_block_google_event_for_user,
        delete_calendar_event_by_google_id,
        get_study_block_google_events_by_plan,
        get_study_blocks_by_plan,
        get_study_plan,
        refresh_study_plan_google_status,
    )

    plan = get_study_plan(user_id, plan_id)
    if not plan:
        raise ValueError("Nie znaleziono planu nauki.")

    links = get_study_block_google_events_by_plan(plan_id)
    checked_count = 0
    missing_count = 0
    still_exists_count = 0

    for link in links:
        event_id = link.get("google_event_id")
        if not event_id:
            continue

        checked_count += 1
        participant_id = int(link["user_id"])
        block_id = int(link["block_id"])

        if google_event_exists(participant_id, event_id):
            still_exists_count += 1
            continue

        missing_count += 1
        clear_study_block_google_event_for_user(block_id, participant_id)
        delete_calendar_event_by_google_id(participant_id, event_id)

    refreshed_plan = refresh_study_plan_google_status(user_id, plan_id)

    return {
        "message": "Status Google Calendar został zsynchronizowany.",
        "checked_count": checked_count,
        "missing_count": missing_count,
        "still_exists_count": still_exists_count,
        "plan": refreshed_plan,
        "blocks": get_study_blocks_by_plan(user_id, plan_id),
    }


def delete_plan_from_google(user_id: int, plan_id: int):
    from db import (
        clear_study_block_google_event_for_user,
        delete_calendar_event_by_google_id,
        get_study_block_google_events_by_plan,
        get_study_blocks_by_plan,
        get_study_plan,
        refresh_study_plan_google_status,
    )

    plan = get_study_plan(user_id, plan_id)
    if not plan:
        raise ValueError("Nie znaleziono planu nauki.")

    links = get_study_block_google_events_by_plan(plan_id)
    deleted_count = 0
    already_missing_count = 0
    skipped_count = 0

    for link in links:
        event_id = link.get("google_event_id")
        if not event_id:
            skipped_count += 1
            continue

        participant_id = int(link["user_id"])
        block_id = int(link["block_id"])

        deleted = delete_google_event(participant_id, event_id)
        if deleted:
            deleted_count += 1
        else:
            already_missing_count += 1

        clear_study_block_google_event_for_user(block_id, participant_id)
        delete_calendar_event_by_google_id(participant_id, event_id)

    refreshed_plan = refresh_study_plan_google_status(user_id, plan_id)

    return {
        "message": "Plan został usunięty z Google Calendar. Lokalny plan został zachowany.",
        "deleted_count": deleted_count,
        "already_missing_count": already_missing_count,
        "skipped_count": skipped_count,
        "plan": refreshed_plan,
        "blocks": get_study_blocks_by_plan(user_id, plan_id),
    }
