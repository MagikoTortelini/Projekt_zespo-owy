from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo
from Map_distance_API import Distance_and_tiem

from Google_calendar_api import add_event, delete_google_event, google_event_exists
from db import get_group_planning_preferences

TIMEZONE = ZoneInfo("Europe/Warsaw")
SEARCH_STEP_MINUTES = 15
DEFAULT_AFTER_CALENDAR_EVENT_BREAK_MINUTES = 15
DEFAULT_BEFORE_CALENDAR_EVENT_BREAK_MINUTES = 0
SOFT_EXTENDED_END_HOUR = 23

# ══════════════════════════════════════════════════════════════════════════════
# HIERARCHIA PUNKTACJI  (od najważniejszego do najmniej ważnego)
#
# POZIOM 1 – Rozłożenie bloków po dniach  (dominujący sygnał, max ±90)
#   Cel: nie kumulować 3 bloków jednego dnia gdy inne dni są puste.
#   Każdy blok już zaplanowany na dany dzień silnie obniża jego atrakcyjność.
#
# POZIOM 2 – Obciążenie kalendarza danego dnia  (max ±40)
#   Cel: kierować naukę na dni z mniejszą liczbą zajęć.
#   Stopniowana skala → 0 min zajęć = +40, >5h zajęć = -40.
#
# POZIOM 3 – Odległość od deadline  (max +20)
#   Cel: lekka preferencja dla wcześniejszych dni, żeby nie zostawiać
#   wszystkiego na ostatni tydzień.
#
# POZIOM 4 – Preferencje godzinowe  (max +25)
#   Cel: w ramach wybranego dnia preferuj okno [start, end] i bliskie godziny.
#   Celowo SŁABSZY niż poziomy 1-3, żeby godzina nie "przyklejała" bloków
#   do tych samych dni tylko dlatego że mają wolny slot o 8:00.
#
# POZIOM 5 – Kontekst slotu  (max ±40)
#   Sygnały lokalne: czy slot jest zaraz po/przed zajęciami, ile bloków pod rząd.
#
# POZIOM 6 – Kary bezwzględne  (-25)
#   Godziny wieczorne/poranne – zawsze kara niezależnie od reszty.
# ══════════════════════════════════════════════════════════════════════════════

# POZIOM 1 – rozkład bloków po dniach
# Ile bloków nauki już zaplanowano na ten dzień → punkty
DAY_BLOCKS_PLACED_SCORES = [
    40,  # 0 bloków → +40  (dzień całkowicie wolny od nauki)
    0,  # 1 blok   →   0  (neutralny)
    -35,  # 2 bloki  → -35  (lepiej szukać gdzie indziej)
    -90,  # 3+ bloki → -90  (mocna kara, tylko gdy absolutnie nie ma opcji)
]

# POZIOM 2 – obciążenie kalendarza (minuty zajęć w dniu → punkty)
DAY_LOAD_THRESHOLDS = [
    (0, 40),  # dzień pusty kalendarza       → +40
    (30, 25),  # ≤ 30 min zajęć               → +25
    (60, 15),  # ≤ 60 min                     → +15
    (120, 5),  # ≤ 120 min (2 h)              →  +5
    (180, 0),  # ≤ 180 min (3 h)              →   0
    (300, -20),  # ≤ 300 min (5 h)              → -20
    (float("inf"), -40),  # > 300 min (bardzo ciężki)    → -40
]

# POZIOM 3 – bliskość do dziś (kara za każdy dzień oddalenia od teraz)
# -7 pkt za każdy dzień od dziś → dzień z 1h zajęć za 3 dni bije pusty dzień za tydzień.
# Dzięki temu bloki nie "czekają" na idealny dzień w przyszłości gdy jest dostępny
# wystarczająco dobry dzień wcześniej.
SCORE_DAYS_FROM_NOW_PENALTY = -7  # pkt × liczba pełnych dni od dziś

# POZIOM 4 – preferencje godzinowe (celowo obniżone)
SCORE_NEAR_PREFERRED_HOUR = 15  # start ≤ 1 h od preferred_start_hour
SCORE_IN_PREFERRED_RANGE = 10  # start w oknie [preferred_start, preferred_end]

# POZIOM 5 – kontekst slotu
SCORE_COMMUTE_SHORT = 15  # dojazd < 15 min
SCORE_COMMUTE_MEDIUM = 5  # dojazd 15-30 min
SCORE_COMMUTE_LONG = -20  # dojazd > 30 min
SCORE_LARGE_GAP_BEFORE = 10  # przerwa > 2 h od poprzedniego eventu/bloku
SCORE_AFTER_CLASS = -15  # blok zaraz po zajęciach (≤ 30 min po końcu eventu)
SCORE_BEFORE_CLASS = -15  # blok zaraz przed zajęciami (≤ 30 min przed startem)
SCORE_TWO_BLOCKS_IN_ROW = -20  # 2 bloki nauki pod rząd (tego samego dnia)
SCORE_THREE_BLOCKS_IN_ROW = -40  # 3+ bloki nauki pod rząd

# POZIOM 6 – kary bezwzględne
SCORE_LATE_EVENING = -25  # start ≥ 22:00
SCORE_EARLY_MORNING = -25  # start < 07:00

# Tolerancja "zaraz przed/po" wydarzeniu (minuty)
ADJACENT_TOLERANCE_MINUTES = 30
# "Blisko preferowanej godziny" = w ciągu ilu godzin
NEAR_PREFERRED_HOUR_TOLERANCE = 1


@lru_cache(maxsize=256)
def get_cached_distance_and_time(loc1: str, loc2: str):
    return Distance_and_tiem(loc1, loc2)


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


def is_free(
        candidate_start: datetime,
        candidate_end: datetime,
        candidate_location: str,
        busy_slots: list[dict],
        commute_extra_buffer_minutes: int,
        break_minutes: int,  # <--- Dodane preferencje przerwy po bloku nauki
) -> bool:
    car_speed_m_s=8.3
    for slot in busy_slots:
        slot_start = slot["start"]
        slot_end = slot["end"]
        slot_location = slot.get("location", "")

        # Podstawowe nakładanie się slotów
        if overlaps(candidate_start, candidate_end, slot_start, slot_end):
            return False

        if not slot_location or not candidate_location:
            continue

        # Logika dla wydarzenia, które kończy się przed naszym kandydatem
        if slot_end <= candidate_start:
            result = get_cached_distance_and_time(slot_location, candidate_location)
            if result is None:
                continue

            distance, travel_seconds = result
            if distance > 2000:
                travel_seconds = int(distance / car_speed_m_s)

            # Uwzględniamy bufor dojazdu oraz preferowaną przerwę użytkownika po bloku (jeśli to był study_block)
            total_buffer = commute_extra_buffer_minutes if distance > 200 else 0
            if slot.get("source") in {"study_block", "planned_study"}:
                total_buffer += break_minutes

            arrival_time = slot_end + timedelta(seconds=travel_seconds) + timedelta(minutes=total_buffer)
            if arrival_time > candidate_start:
                return False

        # Logika dla wydarzenia, które zaczyna się po naszym kandydacie
        if candidate_end <= slot_start:
            result = get_cached_distance_and_time(candidate_location, slot_location)
            if result is None:
                continue

            distance, travel_seconds = result
            if distance > 2000:
                travel_seconds = int(distance / car_speed_m_s)

            # Idąc na kolejne zajęcia, kandydat potrzebuje czasu na dojazd + ewentualny bufor
            total_buffer = commute_extra_buffer_minutes if distance > 200 else 0
            # Dodatkowo nasz kandydat potrzebuje własnej przerwy po nauce zanim w ogóle zacznie jechać
            total_buffer += break_minutes

            leave_time = candidate_end + timedelta(seconds=travel_seconds) + timedelta(minutes=total_buffer)
            if leave_time > slot_start:
                return False
    return True


def get_busy_slots_from_saved_events(events: list[dict], ) -> list[dict]:
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


def _day_busy_minutes(day_date, calendar_events: list[dict]) -> float:
    """Suma minut wszystkich eventów kalendarza w danym dniu."""
    total = 0.0
    for e in calendar_events:
        try:
            e_s = parse_datetime(e["start_time"])
            e_e = parse_datetime(e["end_time"])
        except Exception:
            continue
        if e_s.date() == day_date and e_e > e_s:
            total += (e_e - e_s).total_seconds() / 60
    return total


def _day_load_score(busy_minutes: float) -> int:
    """Punkty za obciążenie kalendarza danego dnia."""
    for threshold, pts in DAY_LOAD_THRESHOLDS:
        if busy_minutes <= threshold:
            return pts
    return DAY_LOAD_THRESHOLDS[-1][1]


def score_day(
        day: datetime,
        *,
        now: datetime,
        calendar_events: list[dict],
        daily_placed: dict[str, int],
        block_minutes: int,
) -> int:
    """
    Ocenia DZIEŃ jako kandydata do umieszczenia bloku nauki.
    Wyższy wynik = lepszy dzień.

    Pytanie: "W który dzień wstawić następny blok?"

    Hierarchia:
      1. Ile bloków już zaplanowano na ten dzień  (dominujące)
      2. Odległość od dziś                        (silne – nie czekaj na lepszy dzień w przyszłości)
      3. Obciążenie kalendarza                    (preferencja lżejszych dni)
    """
    day_key = day.date().isoformat()
    blocks_today = daily_placed.get(day_key, 0) // max(block_minutes, 1)

    # 1. Rozkład – im więcej bloków już dziś, tym gorzej
    idx = min(blocks_today, len(DAY_BLOCKS_PLACED_SCORES) - 1)
    score = DAY_BLOCKS_PLACED_SCORES[idx]

    # 2. Bliskość do dziś – kara za każdy dzień od teraz
    days_from_now = max((day.date() - now.date()).days, 0)
    score += SCORE_DAYS_FROM_NOW_PENALTY * days_from_now

    # 3. Obciążenie kalendarza
    busy_min = _day_busy_minutes(day.date(), calendar_events)
    score += _day_load_score(busy_min)

    return score


def score_slot(
        candidate_start,
        candidate_end,
        *,
        preferred_start_hour,
        preferred_end_hour,
        commute_extra_buffer_minutes,
        calendar_events,
        placed_study_blocks,
        study_location,
        busy_slots,
) -> int:
    score = 0
    start_hour = candidate_start.hour + candidate_start.minute / 60.0

    if candidate_start.hour >= 22:
        score += SCORE_LATE_EVENING
    if candidate_start.hour < 7:
        score += SCORE_EARLY_MORNING

    if preferred_start_hour <= candidate_start.hour < preferred_end_hour:
        score += SCORE_IN_PREFERRED_RANGE
    if abs(start_hour - preferred_start_hour) <= NEAR_PREFERRED_HOUR_TOLERANCE:
        score += SCORE_NEAR_PREFERRED_HOUR

    all_ends_before = []
    for e in calendar_events:
        try:
            e_end = parse_datetime(e["end_time"])
            if e_end <= candidate_start:
                all_ends_before.append(e_end)
        except Exception:
            continue
    for b_start, b_end in placed_study_blocks:
        if b_end <= candidate_start:
            all_ends_before.append(b_end)
    if all_ends_before:
        gap_min = (candidate_start - max(all_ends_before)).total_seconds() / 60
        if gap_min > 120:
            score += SCORE_LARGE_GAP_BEFORE

    for e in calendar_events:
        try:
            e_start = parse_datetime(e["start_time"])
            e_end = parse_datetime(e["end_time"])
            if 0 <= (candidate_start - e_end).total_seconds() / 60 <= ADJACENT_TOLERANCE_MINUTES:
                score += SCORE_AFTER_CLASS
                break
            if 0 <= (e_start - candidate_end).total_seconds() / 60 <= ADJACENT_TOLERANCE_MINUTES:
                score += SCORE_BEFORE_CLASS
                break
        except Exception:
            continue

    consecutive = 0
    check_start = candidate_start
    same_day_blocks = [b for b in placed_study_blocks if b[0].date() == candidate_start.date()]

    for b_start, b_end in sorted(same_day_blocks, key=lambda x: x[1], reverse=True):
        gap = (check_start - b_end).total_seconds() / 60
        if 0 <= gap <= 60:
            consecutive += 1
            check_start = b_start
        elif b_end <= check_start:
            break

    if consecutive >= 2:
        score += SCORE_THREE_BLOCKS_IN_ROW
    elif consecutive == 1:
        score += SCORE_TWO_BLOCKS_IN_ROW

    score += get_location_score(
        candidate_start,
        candidate_end,
        study_location,
        busy_slots,
    )

    return score

def score_commute(from_loc, to_loc):
    if not from_loc or not to_loc:
        return 0

    result = get_cached_distance_and_time(from_loc, to_loc)

    if not result:
        return 0

    _, travel_seconds = result

    travel_minutes = travel_seconds / 60

    if travel_minutes < 15:
        return SCORE_COMMUTE_SHORT
    elif travel_minutes <= 30:
        return SCORE_COMMUTE_MEDIUM
    else:
        return SCORE_COMMUTE_LONG



def get_location_score(
        candidate_start,
        candidate_end,
        study_location,
        busy_slots,
):
    score = 0

    prev_slot = None
    next_slot = None

    for slot in busy_slots:

        if slot["end"] <= candidate_start:
            if prev_slot is None or slot["end"] > prev_slot["end"]:
                prev_slot = slot

        if slot["start"] >= candidate_end:
            if next_slot is None or slot["start"] < next_slot["start"]:
                next_slot = slot

    if prev_slot:
        score += score_commute(
            prev_slot.get("location", ""),
            study_location,
        )

    if next_slot:
        score += score_commute(
            study_location,
            next_slot.get("location", ""),
        )

    return score


def generate_study_plan_for_users(
        owner_user_id: int,
        participant_user_ids: list[int],
        total_hours,
        deadline_str,
        location: str = "",
) -> list[dict]:
    from db import get_calendar_events_for_users, get_study_blocks, normalize_participant_user_ids

    participant_ids = normalize_participant_user_ids(owner_user_id, participant_user_ids)
    preferences = get_group_planning_preferences(owner_user_id, participant_ids)

    preferred_start_hour = int(preferences["preferred_start_hour"])
    preferred_end_hour = int(preferences["preferred_end_hour"])
    block_minutes = int(preferences["block_minutes"])
    break_minutes = int(preferences["break_minutes"])
    max_daily_study_minutes = int(preferences["max_daily_study_minutes"])
    commute_extra_buffer_minutes = int(preferences["commute_extra_buffer_minutes"])

    # WYMUSZENIE SIATKI 15 MINUT: Zaokrąglamy czas trwania przerwy w górę do wielokrotności SEARCH_STEP_MINUTES
    if break_minutes > 0 and break_minutes % SEARCH_STEP_MINUTES != 0:
        break_minutes = ((break_minutes // SEARCH_STEP_MINUTES) + 1) * SEARCH_STEP_MINUTES

    is_group = len(participant_ids) > 1
    mode_key = "group_preference_mode" if is_group else "solo_preference_mode"
    is_soft = preferences.get(mode_key, "hard") == "soft"

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
    )

    for participant_id in participant_ids:
        existing_blocks = get_study_blocks(participant_id)
        busy_slots.extend(get_busy_slots_from_saved_blocks(existing_blocks))

    def _slot_end_limit(day: datetime) -> datetime:
        if is_soft:
            return day.replace(hour=SOFT_EXTENDED_END_HOUR, minute=0, second=0, microsecond=0)
        return day.replace(hour=preferred_end_hour, minute=0, second=0, microsecond=0)

    def _day_start_time(day: datetime) -> datetime:
        ds = day.replace(hour=preferred_start_hour, minute=0, second=0, microsecond=0)
        if day.date() == now.date() and ds < now:
            ds = round_up_datetime(now + timedelta(minutes=5))
        return ds

    def _find_best_slot_in_day(
            day: datetime,
            current_block_minutes: int,
    ) -> tuple[datetime, datetime] | None:
        best_start: datetime | None = None
        best_end: datetime | None = None
        best_sc: int = int(-1e9)

        end_limit = _slot_end_limit(day)
        t = _day_start_time(day)

        # OPTYMALIZACJA: Filtrujemy busy_slots tylko do obecnego dnia,
        # dzięki czemu get_location_score i is_free działają błyskawicznie
        day_date = day.date()
        day_busy_slots = [
            slot for slot in busy_slots
            if slot["start"].date() == day_date or slot["end"].date() == day_date
        ]

        while t < end_limit:
            cand_end = t + timedelta(minutes=current_block_minutes)
            if cand_end > end_limit or cand_end > deadline:
                break

            if is_free(
                    candidate_start=t,
                    candidate_end=cand_end,
                    candidate_location=location,
                    busy_slots=day_busy_slots,
                    commute_extra_buffer_minutes=commute_extra_buffer_minutes,
                    break_minutes=break_minutes
            ):
                sc = score_slot(
                    t,
                    cand_end,
                    preferred_start_hour=preferred_start_hour,
                    preferred_end_hour=preferred_end_hour,
                    commute_extra_buffer_minutes=commute_extra_buffer_minutes,
                    calendar_events=saved_events,
                    placed_study_blocks=study_blocks,
                    study_location=location,
                    busy_slots=day_busy_slots,
                )
                if sc > best_sc:
                    best_sc = sc
                    best_start = t
                    best_end = cand_end
            t += timedelta(minutes=SEARCH_STEP_MINUTES)

        if best_start is None:
            return None
        return (best_start, best_end)

    study_blocks: list[tuple[datetime, datetime]] = []
    remaining_minutes = total_hours_int * 60
    daily_placed: dict[str, int] = {}

    while remaining_minutes > 0:
        best_day: datetime | None = None
        best_day_score: int = int(-1e9)

        d = now.replace(hour=0, minute=0, second=0, microsecond=0)
        while d <= deadline:
            day_key = d.date().isoformat()
            day_used = daily_placed.get(day_key, 0)
            if day_used >= max_daily_study_minutes:
                d += timedelta(days=1)
                continue

            current_block_minutes = min(
                block_minutes,
                remaining_minutes,
                max_daily_study_minutes - day_used,
            )

            if _find_best_slot_in_day(d, current_block_minutes) is None:
                d += timedelta(days=1)
                continue

            ds = score_day(
                d,
                now=now,
                calendar_events=saved_events,
                daily_placed=daily_placed,
                block_minutes=block_minutes,
            )
            if ds > best_day_score:
                best_day_score = ds
                best_day = d

            d += timedelta(days=1)

        if best_day is None:
            break

        day_key = best_day.date().isoformat()
        day_used = daily_placed.get(day_key, 0)
        current_block_minutes = min(
            block_minutes,
            remaining_minutes,
            max_daily_study_minutes - day_used,
        )

        result = _find_best_slot_in_day(best_day, current_block_minutes)
        if result is None:
            break

        best_start, best_end = result

        study_blocks.append((best_start, best_end))
        busy_slots.append(
            {
                "start": best_start,
                "end": best_end,
                "location": location,
                "source": "study_block",
            }
        )

        placed_minutes = int((best_end - best_start).total_seconds() // 60)
        remaining_minutes -= placed_minutes

        # Zapisujemy dokładny czas bloku + przerwy (która już jest wielokrotnością 15 minut)
        daily_placed[day_key] = daily_placed.get(day_key, 0) + placed_minutes + break_minutes

        if break_minutes > 0:
            break_end = best_end + timedelta(minutes=break_minutes)
            busy_slots.append(
                {
                    "start": best_end,
                    "end": break_end,
                    "location": location,
                    "source": "break",
                }
            )

    if remaining_minutes > 0:
        planned_hours = (total_hours_int * 60 - remaining_minutes) / 60
        raise ValueError(
            f"Nie udało się zaplanować całego czasu dla wszystkich uczestników. "
            f"Zaplanowano {planned_hours:.2f}h z {total_hours_int}h. "
            "Spróbuj wydłużyć deadline albo zakres godzin nauki."
        )

    return sorted(study_blocks, key=lambda b: b[0])


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
