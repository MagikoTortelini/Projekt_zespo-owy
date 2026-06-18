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


SOFT_RELAXATION_LEVELS = [
    {
        "name": "ideal",
        "start_offset": 0,
        "end_offset": 0,
        "daily_extra_minutes": 0,
        "min_block_ratio": 1.0,
        "penalty": 0,
    },
    {
        "name": "extended_hours",
        "start_offset": -1,
        "end_offset": 1,
        "daily_extra_minutes": 0,
        "min_block_ratio": 1.0,
        "penalty": 20,
    },
    {
        "name": "shorter_blocks",
        "start_offset": -2,
        "end_offset": 2,
        "daily_extra_minutes": 0,
        "min_block_ratio": 0.66,
        "penalty": 40,
    },
    {
        "name": "higher_daily_limit",
        "start_offset": -2,
        "end_offset": 2,
        "daily_extra_minutes": 60,
        "min_block_ratio": 0.66,
        "penalty": 60,
    },
    {
        "name": "emergency",
        "start_offset": -4,
        "end_offset": 4,
        "daily_extra_minutes": 120,
        "min_block_ratio": 0.5,
        "penalty": 90,
    },
]


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
    if step_minutes <= 0:
        return dt.replace(second=0, microsecond=0)

    dt = dt.replace(second=0, microsecond=0)
    minute_rest = dt.minute % step_minutes

    if minute_rest == 0:
        return dt

    return dt + timedelta(minutes=step_minutes - minute_rest)


def overlaps(start1: datetime, end1: datetime, start2: datetime, end2: datetime) -> bool:
    return start1 < end2 and start2 < end1


@lru_cache(maxsize=256)
def get_cached_distance_and_time(loc1: str, loc2: str):
    return Distance_and_tiem(loc1, loc2)


def is_free(
    extra_break_minutes,
    candidate_start: datetime,
    candidate_end: datetime,
    candidate_location: str,
    busy_slots,
) -> bool:
    car_speed_m_s = 8.3


    for slot in busy_slots:
        slot_start = slot["start"]
        slot_end = slot["end"]
        slot_location = slot.get("location", "")

        if overlaps(candidate_start, candidate_end, slot_start, slot_end):
            return False

        if not slot_location or not candidate_location:
            continue

        if slot_end <= candidate_start:
            result = get_cached_distance_and_time(slot_location, candidate_location)

            if result is None:
                continue
            else:
                distance, travel_seconds = result

                if distance > 2000:
                    travel_seconds = int(distance / car_speed_m_s)

                if distance > 200:
                    arrival_time = (
                        slot_end
                        + timedelta(seconds=travel_seconds)
                        + timedelta(minutes=extra_break_minutes)
                    )
                else:
                    arrival_time = slot_end + timedelta(seconds=travel_seconds)

                if arrival_time > candidate_start:
                    return False

        if candidate_end <= slot_start:
            result = get_cached_distance_and_time(candidate_location, slot_location)

            if result is None:
                continue

            distance, travel_seconds = result

            if distance > 2000:
                travel_seconds = int(distance / car_speed_m_s)

            if distance > 200:
                leave_time = (
                    candidate_end
                    + timedelta(seconds=travel_seconds)
                    + timedelta(minutes=extra_break_minutes)
                )
            else:
                leave_time = candidate_end + timedelta(seconds=travel_seconds)

            if leave_time > slot_start:
                return False

    return True


def get_busy_slots_from_saved_events(events: list[dict],) -> list[dict]:
    busy_slots = []

    for event in events:
        start_data = event.get("start_time")
        end_data = event.get("end_time")

        if not start_data or not end_data:
            continue

        start_dt = parse_datetime(start_data)
        end_dt = parse_datetime(end_data)

        busy_slots.append(
            {
                "start": start_dt,
                "end": end_dt,
                "location": event.get("location", ""),
                "source": "calendar_event",
            }
        )

    return busy_slots

def get_busy_slots_from_saved_blocks(blocks: list[dict]) -> list[dict]:
    busy_slots = []

    for block in blocks:
        start_data = block.get("start_time")
        end_data = block.get("end_time")

        if not start_data or not end_data:
            continue

        busy_slots.append(
            {
                "start": parse_datetime(start_data),
                "end": parse_datetime(end_data),
                "location": block.get("location", ""),
                "source": "study_block",
            }
        )

    return busy_slots

def get_planned_study_minutes_for_day(busy_slots: list[dict], day: datetime) -> int:
    total = 0
    day_key = day.date()

    for slot in busy_slots:
        if slot.get("source") not in {"study_block", "planned_study"}:
            continue

        if slot["start"].date() == day_key:
            total += int((slot["end"] - slot["start"]).total_seconds() / 60)

    return total

def score_slot(
    candidate_start: datetime,
    candidate_end: datetime,
    preferences: dict,
    deadline: datetime,
    relaxation_level: dict | None = None,
) -> int:
    score = 100

    relaxation_level = relaxation_level or {
        "name": "hard",
        "penalty": 0,
    }

    preferred_start_hour = int(preferences["preferred_start_hour"])
    preferred_end_hour = int(preferences["preferred_end_hour"])
    preferred_block_minutes = int(preferences["block_minutes"])

    duration_minutes = int((candidate_end - candidate_start).total_seconds() / 60)

    # Kara za poziom relaksacji preferencji w trybie soft.
    # Im bardziej algorytm łamie preferencje, tym niższa ocena slotu.
    score -= int(relaxation_level.get("penalty", 0))

    # Bonus za zmieszczenie się w preferowanych godzinach.
    if candidate_start.hour >= preferred_start_hour and candidate_end.hour <= preferred_end_hour:
        score += 60
    else:
        score -= 50

    # Kara za długość bloku oddaloną od preferowanej.
    duration_diff = abs(duration_minutes - preferred_block_minutes)
    score -= duration_diff // 3

    # Bonus za planowanie wcześniej, a nie na ostatnią chwilę.
    days_to_deadline = (deadline.date() - candidate_start.date()).days
    score += max(0, days_to_deadline * 2)

    # Preferowane pory dnia.
    if 8 <= candidate_start.hour <= 12:
        score += 30
    elif 13 <= candidate_start.hour <= 17:
        score += 20
    elif 18 <= candidate_start.hour <= 20:
        score += 5

    # Kary za niekomfortowe godziny.
    if candidate_start.hour >= 21:
        score -= 35

    if candidate_start.hour < 6:
        score -= 60

    # Idealny poziom bez łamania preferencji dostaje dodatkowy bonus.
    if relaxation_level.get("name") == "ideal":
        score += 30

    return score

def hard_sort_algorithm(candidates: list[dict]) -> list[dict]:
    return sorted(
        candidates,
        key=lambda candidate: candidate["start"],
    )


def soft_score_sort_algorithm(candidates: list[dict]) -> list[dict]:
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate["score"],
            candidate["duration_minutes"],
            -candidate["start"].timestamp(),
        ),
        reverse=True,
    )


def collect_candidates_for_current_state(
    owner_user_id: int,
    participant_ids: list[int],
    preferences: dict,
    deadline: datetime,
    location: str,
    busy_slots: list[dict],
    remaining_minutes: int,
    mode: str,
    relaxation_level: dict | None = None,
) -> list[dict]:
    preferred_start_hour = int(preferences["preferred_start_hour"])
    preferred_end_hour = int(preferences["preferred_end_hour"])
    block_minutes = int(preferences["block_minutes"])
    base_max_daily_study_minutes = int(preferences["max_daily_study_minutes"])
    commute_extra_buffer_minutes = 0
    for p_id in participant_ids:
        preferences = get_user_preferences(p_id)
        buffor = int(preferences["commute_extra_buffer_minutes"])
        if buffor > commute_extra_buffer_minutes:
            commute_extra_buffer_minutes = buffor

    if relaxation_level is None:
        relaxation_level = {
            "name": "hard",
            "start_offset": 0,
            "end_offset": 0,
            "daily_extra_minutes": 0,
            "min_block_ratio": 1.0,
            "penalty": 0,
        }

    now = datetime.now(TIMEZONE)
    current_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    candidates = []

    while current_day <= deadline:
        if mode == "hard":
            day_start_hour = preferred_start_hour
            day_end_hour = preferred_end_hour
            min_block_minutes = min(block_minutes, remaining_minutes)
            max_daily_study_minutes = base_max_daily_study_minutes
        else:
            day_start_hour = max(
                0,
                preferred_start_hour + int(relaxation_level["start_offset"]),
            )
            day_end_hour = min(
                23,
                preferred_end_hour + int(relaxation_level["end_offset"]),
            )
            min_block_minutes = max(
                SEARCH_STEP_MINUTES,
                int(block_minutes * float(relaxation_level["min_block_ratio"])),
            )
            max_daily_study_minutes = (
                base_max_daily_study_minutes
                + int(relaxation_level["daily_extra_minutes"])
            )

        day_start = current_day.replace(hour=day_start_hour, minute=0)
        day_end = current_day.replace(hour=day_end_hour, minute=0)

        # Tryb awaryjny pozwala szukać prawie przez cały dzień.
        if mode == "soft" and relaxation_level.get("name") == "emergency":
            day_start = current_day.replace(hour=0, minute=0)
            day_end = current_day.replace(hour=23, minute=59)

        if current_day.date() == now.date() and day_start < now:
            day_start = round_up_datetime(now + timedelta(minutes=5))

        if current_day.date() == deadline.date():
            day_end = min(day_end, deadline)

        daily_planned = get_planned_study_minutes_for_day(busy_slots, current_day)
        daily_left = max_daily_study_minutes - daily_planned

        if daily_left <= 0:
            current_day += timedelta(days=1)
            continue

        current_time = day_start

        while current_time < day_end:
            max_possible_block = min(
                block_minutes,
                remaining_minutes,
                daily_left,
            )

            possible_duration = max_possible_block

            while possible_duration >= min_block_minutes:
                candidate_start = current_time
                candidate_end = candidate_start + timedelta(minutes=possible_duration)

                if candidate_end > day_end or candidate_end > deadline:
                    possible_duration -= SEARCH_STEP_MINUTES
                    continue

                if is_free(commute_extra_buffer_minutes,candidate_start, candidate_end, location, busy_slots):
                    candidates.append(
                        {
                            "start": candidate_start,
                            "end": candidate_end,
                            "duration_minutes": possible_duration,
                            "score": score_slot(
                                candidate_start,
                                candidate_end,
                                preferences,
                                deadline,
                                relaxation_level,
                            ),
                            "relaxation": relaxation_level["name"],
                        }
                    )

                # Hard nie skraca bloków. Soft może próbować krótsze warianty.
                if mode == "hard":
                    break

                possible_duration -= SEARCH_STEP_MINUTES

            current_time += timedelta(minutes=SEARCH_STEP_MINUTES)

        current_day += timedelta(days=1)

    return candidates

def generate_plan_hard(
    owner_user_id: int,
    participant_ids: list[int],
    preferences: dict,
    deadline: datetime,
    location: str,
    busy_slots: list[dict],
    total_minutes: int,
) -> list[tuple[datetime, datetime]]:
    study_blocks = []
    remaining_minutes = total_minutes

    while remaining_minutes > 0:
        candidates = collect_candidates_for_current_state(
            owner_user_id=owner_user_id,
            participant_ids=participant_ids,
            preferences=preferences,
            deadline=deadline,
            location=location,
            busy_slots=busy_slots,
            remaining_minutes=remaining_minutes,
            mode="hard",
        )

        if not candidates:
            planned_hours = (total_minutes - remaining_minutes) / 60
            raise ValueError(
                f"Nie udało się zaplanować całego czasu w trybie hard. "
                f"Zaplanowano {planned_hours:.2f} h z {total_minutes / 60:.2f} h. "
                "Tryb hard nie łamie preferencji użytkownika. "
                "Zwiększ zakres godzin, limit dzienny albo wydłuż deadline."
            )

        sorted_candidates = hard_sort_algorithm(candidates)
        best = sorted_candidates[0]

        study_blocks.append((best["start"], best["end"]))

        busy_slots.append(
            {
                "start": best["start"],
                "end": best["end"],
                "location": location,
                "source": "planned_study",
            }
        )

        remaining_minutes -= best["duration_minutes"]

    return study_blocks

def generate_plan_soft(
    owner_user_id: int,
    participant_ids: list[int],
    preferences: dict,
    deadline: datetime,
    location: str,
    busy_slots: list[dict],
    total_minutes: int,
) -> list[tuple[datetime, datetime]]:
    """
    SOFT planner działa inaczej niż HARD.

    HARD:
        - traktuje preferencje jako twarde ograniczenia,
        - jeśli nie da się zaplanować całości, zwraca błąd.

    SOFT:
        - traktuje preferencje jako wskazówki,
        - najpierw próbuje plan idealny,
        - jeśli nie da się zaplanować całego czasu, przechodzi do kolejnego poziomu relaksacji,
        - zwraca pierwszy kompletny plan, który udało się wygenerować.
    """

    best_partial_plan = []
    best_partial_minutes = 0
    best_partial_level_name = None

    for relaxation_level in SOFT_RELAXATION_LEVELS:
        level_busy_slots = [slot.copy() for slot in busy_slots]
        level_study_blocks = []
        level_remaining_minutes = total_minutes

        while level_remaining_minutes > 0:
            candidates = collect_candidates_for_current_state(
                owner_user_id=owner_user_id,
                participant_ids=participant_ids,
                preferences=preferences,
                deadline=deadline,
                location=location,
                busy_slots=level_busy_slots,
                remaining_minutes=level_remaining_minutes,
                mode="soft",
                relaxation_level=relaxation_level,
            )

            if not candidates:
                break

            sorted_candidates = soft_score_sort_algorithm(candidates)
            best = sorted_candidates[0]

            level_study_blocks.append((best["start"], best["end"]))

            level_busy_slots.append(
                {
                    "start": best["start"],
                    "end": best["end"],
                    "location": location,
                    "source": "planned_study",
                }
            )

            level_remaining_minutes -= best["duration_minutes"]

        planned_minutes = total_minutes - level_remaining_minutes

        if planned_minutes > best_partial_minutes:
            best_partial_minutes = planned_minutes
            best_partial_plan = level_study_blocks
            best_partial_level_name = relaxation_level["name"]

        # Najważniejszy warunek:
        # soft zatrzymuje się dopiero wtedy, gdy udało się zaplanować CAŁY wymagany czas.
        if level_remaining_minutes <= 0:
            return sorted(level_study_blocks, key=lambda block: block[0])

    planned_hours = best_partial_minutes / 60

    raise ValueError(
        f"Nie udało się zaplanować całego czasu nawet w trybie soft. "
        f"Zaplanowano {planned_hours:.2f} h z {total_minutes / 60:.2f} h. "
        f"Najlepszy częściowy wynik uzyskano na poziomie: {best_partial_level_name}. "
        "Brakuje realnych wolnych terminów przed deadline."
    )


def generate_study_plan_for_users_basic(
    owner_user_id: int,
    participant_user_ids: list[int],
    total_hours,
    deadline_str,
    location,
) -> list[tuple[datetime, datetime]]:
    from db import get_calendar_events_for_users, get_study_blocks, normalize_participant_user_ids

    participant_ids = normalize_participant_user_ids(owner_user_id, participant_user_ids)
    preferences = get_user_preferences(owner_user_id)

    total_hours_int = int(total_hours)
    if total_hours_int <= 0:
        raise ValueError("Liczba godzin nauki musi być większa od zera.")

    if not deadline_str:
        raise ValueError("Brak deadline.")

    now = datetime.now(TIMEZONE)
    deadline = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)

    if deadline <= now:
        raise ValueError("Deadline musi być w przyszłości.")

    preferred_start_hour = int(preferences["preferred_start_hour"])
    preferred_end_hour = int(preferences["preferred_end_hour"])

    if preferred_start_hour >= preferred_end_hour:
        raise ValueError("Godzina początku musi być mniejsza niż godzina końca.")

    saved_events = get_calendar_events_for_users(participant_ids)
    busy_slots = get_busy_slots_from_saved_events(saved_events)

    for participant_id in participant_ids:
        existing_blocks = get_study_blocks(participant_id)
        busy_slots.extend(get_busy_slots_from_saved_blocks(existing_blocks))

    total_minutes = total_hours_int * 60

    is_group = len(participant_ids) > 1

    mode = (
        preferences["group_preference_mode"]
        if is_group
        else preferences["solo_preference_mode"]
    )

    mode = mode if mode in {"hard", "soft"} else "hard"

    if mode == "soft":
        return generate_plan_soft(
            owner_user_id=owner_user_id,
            participant_ids=participant_ids,
            preferences=preferences,
            deadline=deadline,
            location=location,
            busy_slots=busy_slots,
            total_minutes=total_minutes,
        )

    return generate_plan_hard(
        owner_user_id=owner_user_id,
        participant_ids=participant_ids,
        preferences=preferences,
        deadline=deadline,
        location=location,
        busy_slots=busy_slots,
        total_minutes=total_minutes,
    )


def generate_study_plan_from_saved_events(user_id: int, total_hours, deadline_str):
    return generate_study_plan_for_users_basic(user_id, [user_id], total_hours, deadline_str, "")


def save_blocks_to_google_basic(user_id: int, blocks: list[dict]):
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
            participant_label = (
                participant.get("email")
                or participant.get("display_name")
                or f"user {participant_id}"
            )

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


def sync_plan_google_state_basic(user_id: int, plan_id: int):
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


def delete_plan_from_google_basic(user_id: int, plan_id: int):
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
