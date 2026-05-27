from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse

from Google_auth import (
    clear_credentials,
    generate_auth_url,
    handle_callback,
    is_authenticated,
)
from Google_calendar_api import get_events, reset_sync_token, sync_events
from Study_planner import (
    delete_plan_from_google,
    generate_study_plan_for_users,
    save_blocks_to_google,
    sync_plan_google_state,
)
from db import (
    cleanup_local_users,
    clear_calendar_events,
    delete_study_plan_locally,
    get_calendar_events,
    get_calendar_events_between,
    get_study_blocks,
    get_study_blocks_by_plan,
    get_study_plan,
    get_study_plans,
    get_study_stats,
    list_available_participants,
    normalize_participant_user_ids,
    get_user,
    get_user_preferences,
    init_db,
    insert_imported_events,
    list_users,
    save_study_blocks,
    save_user_preferences,
    set_study_block_done,
    set_study_plan_pushed,
    user_exists,
)

app = FastAPI(title="Study Planner", version="1.2.0")

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "templates" / "index.html"
COOKIE_NAME = "study_user_id"


@app.on_event("startup")
def startup():
    init_db()


def get_current_user_id_optional(request: Request) -> int | None:
    raw_user_id = request.cookies.get(COOKIE_NAME)

    if raw_user_id and raw_user_id.isdigit():
        user_id = int(raw_user_id)
        if user_exists(user_id):
            user = get_user(user_id)
            if user and (user.get("google_sub") or user.get("email")):
                return user_id

    return None


def get_current_user_id(request: Request, response: Response | None = None) -> int:
    user_id = get_current_user_id_optional(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Najpierw zaloguj się przez Google.")
    return user_id


def set_user_cookie(response: Response, user_id: int):
    response.set_cookie(
        key=COOKIE_NAME,
        value=str(user_id),
        httponly=True,
        samesite="lax",
    )


def validate_date_range(start_date: str | None, end_date: str | None):
    if not start_date or not end_date:
        raise HTTPException(status_code=400, detail="Podaj datę początkową i końcową.")

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Niepoprawny format daty. Użyj YYYY-MM-DD.") from error

    if end < start:
        raise HTTPException(status_code=400, detail="Data końcowa nie może być wcześniejsza niż początkowa.")

    return start, end


def build_calendar_preview(events: list[dict], blocks: list[dict], start_date: str, end_date: str):
    items = []

    for event in events:
        items.append(
            {
                "id": f"event-{event.get('id')}",
                "type": "calendar",
                "type_label": "Kalendarz",
                "title": event.get("title") or "(bez nazwy)",
                "start_time": event.get("start_time"),
                "end_time": event.get("end_time"),
                "location": event.get("location") or "",
                "source": event.get("source") or "google",
                "done": None,
                "pushed_to_google": True,
            }
        )

    for block in blocks:
        items.append(
            {
                "id": f"block-{block.get('id')}",
                "type": "study_block",
                "type_label": "Nauka",
                "title": block.get("title") or "Blok nauki",
                "start_time": block.get("start_time"),
                "end_time": block.get("end_time"),
                "location": "",
                "source": "study_block",
                "done": block.get("done"),
                "pushed_to_google": bool(block.get("google_event_id")),
                "plan_id": block.get("plan_id"),
            }
        )

    items = [item for item in items if item.get("start_time")]
    items.sort(key=lambda item: str(item.get("start_time")))

    days = []
    start, end = validate_date_range(start_date, end_date)
    current = start
    while current <= end:
        day_key = current.strftime("%Y-%m-%d")
        day_items = [item for item in items if str(item.get("start_time", "")).startswith(day_key)]
        days.append(
            {
                "date": day_key,
                "items": day_items,
            }
        )
        current += timedelta(days=1)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "days": days,
        "items_count": len(items),
        "calendar_events_count": len(events),
        "study_blocks_count": len(blocks),
    }


@app.get("/")
def home():
    return FileResponse(INDEX_FILE)


@app.get("/status")
def status(request: Request, response: Response):
    user_id = get_current_user_id_optional(request)

    if user_id is None:
        return {
            "authenticated": False,
            "user_id": None,
            "user": None,
        }

    user = get_user(user_id)

    return {
        "authenticated": is_authenticated(user_id),
        "user_id": user_id,
        "user": user,
    }


@app.get("/users")
def users():
    return list_users()


@app.get("/participants")
def participants(request: Request, response: Response):
    user_id = get_current_user_id(request, response)
    return list_available_participants(user_id)


@app.get("/preferences")
def preferences(request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        return get_user_preferences(user_id)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd odczytu preferencji: {error}") from error


@app.post("/preferences")
def update_preferences(data: dict, request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        save_user_preferences(user_id, data)
        return {
            "message": "Preferencje zapisane poprawnie.",
            "preferences": get_user_preferences(user_id),
        }
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd zapisu preferencji: {error}") from error


@app.get("/auth/login")
def login(request: Request):
    auth_url = generate_auth_url()
    return RedirectResponse(auth_url)


@app.get("/auth/logout")
def logout(request: Request):
    user_id = get_current_user_id_optional(request)

    if user_id is not None:
        clear_credentials(user_id)
        reset_sync_token(user_id)

    redirect = RedirectResponse(url="/?logout=success")
    redirect.delete_cookie(COOKIE_NAME)
    return redirect


@app.get("/auth/google/callback")
def callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    try:
        user_id = handle_callback(code, state)
        redirect = RedirectResponse(url="/?login=success")
        set_user_cookie(redirect, user_id)
        return redirect
    except Exception as error:
        return RedirectResponse(url=f"/?login=error&details={str(error)}")


@app.get("/sync")
def sync(request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        return sync_events(user_id)
    except RuntimeError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd synchronizacji: {error}") from error


@app.get("/events/import")
def import_events_to_db(request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        events = get_events(user_id)

        clear_calendar_events(user_id)
        insert_imported_events(user_id, events)

        saved_events = get_calendar_events(user_id)

        return {
            "message": "Zaimportowano wydarzenia do bazy.",
            "count": len(saved_events),
            "events": saved_events,
        }
    except RuntimeError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd importu do bazy: {error}") from error


@app.get("/events")
def events(request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        return get_calendar_events(user_id)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd odczytu wydarzeń z bazy: {error}") from error


@app.get("/calendar-preview")
def calendar_preview(
    request: Request,
    response: Response,
    start_date: str | None = None,
    end_date: str | None = None,
):
    user_id = get_current_user_id(request, response)
    validate_date_range(start_date, end_date)

    try:
        events = get_calendar_events_between(user_id, start_date, end_date)
        blocks = get_study_blocks(user_id, start_date, end_date)
        return build_calendar_preview(events, blocks, start_date, end_date)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd budowania podglądu kalendarza: {error}") from error


@app.get("/study-plans")
def study_plans(request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        return get_study_plans(user_id)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd odczytu planów nauki: {error}") from error


@app.get("/study-plans/{plan_id}/blocks")
def study_plan_blocks(plan_id: int, request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        plan = get_study_plan(user_id, plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="Nie znaleziono planu nauki.")

        return {
            "plan": plan,
            "blocks": get_study_blocks_by_plan(user_id, plan_id),
        }
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd odczytu bloków planu: {error}") from error


@app.post("/study-plans/{plan_id}/push")
def push_existing_study_plan(plan_id: int, request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        plan = get_study_plan(user_id, plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="Nie znaleziono planu nauki.")

        blocks = get_study_blocks_by_plan(user_id, plan_id)
        if not blocks:
            raise HTTPException(status_code=400, detail="Wybrany plan nie ma bloków do wysłania.")

        if plan.get("pushed_to_google"):
            sync_plan_google_state(user_id, plan_id)
            plan = get_study_plan(user_id, plan_id)
            blocks = get_study_blocks_by_plan(user_id, plan_id)

        if plan.get("pushed_to_google"):
            raise HTTPException(
                status_code=409,
                detail="Ten plan został już wysłany do Google Calendar. Ponowne wysyłanie zostało zablokowane, żeby nie tworzyć duplikatów.",
            )

        result = save_blocks_to_google(user_id, blocks)
        refreshed_blocks = get_study_blocks_by_plan(user_id, plan_id)

        refreshed_plan = sync_plan_google_state(user_id, plan_id)["plan"]

        return {
            "message": "Wybrany plan został przetworzony.",
            "pushed_count": len(result["pushed"]),
            "skipped_count": len(result["skipped"]),
            "plan": refreshed_plan,
            "blocks": refreshed_blocks,
        }
    except HTTPException:
        raise
    except RuntimeError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd wysyłania planu do Google: {error}") from error



@app.post("/study-plans/{plan_id}/sync-google")
def sync_existing_study_plan_google_state(plan_id: int, request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        plan = get_study_plan(user_id, plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="Nie znaleziono planu nauki.")

        return sync_plan_google_state(user_id, plan_id)
    except HTTPException:
        raise
    except RuntimeError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd synchronizacji planu z Google: {error}") from error


@app.delete("/study-plans/{plan_id}/google")
def delete_existing_study_plan_from_google(plan_id: int, request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        plan = get_study_plan(user_id, plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="Nie znaleziono planu nauki.")

        return delete_plan_from_google(user_id, plan_id)
    except HTTPException:
        raise
    except RuntimeError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd usuwania planu z Google: {error}") from error

@app.delete("/study-plans/{plan_id}")
def delete_existing_study_plan_locally(plan_id: int, request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        plan = get_study_plan(user_id, plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="Nie znaleziono planu nauki.")

        deleted_plan = delete_study_plan_locally(user_id, plan_id)
        return {
            "message": "Plan został usunięty z aplikacji.",
            "deleted_plan": deleted_plan,
        }
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd usuwania planu z aplikacji: {error}") from error


@app.post("/maintenance/cleanup-local-users")
def cleanup_local_users_endpoint():
    try:
        return cleanup_local_users()
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd czyszczenia lokalnych użytkowników: {error}") from error


@app.get("/study-blocks")
def study_blocks(
    request: Request,
    response: Response,
    start_date: str | None = None,
    end_date: str | None = None,
    plan_id: int | None = None,
):
    user_id = get_current_user_id(request, response)

    try:
        return get_study_blocks(user_id, start_date, end_date, plan_id)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd odczytu bloków nauki: {error}") from error


@app.post("/study-blocks/{block_id}/done")
def update_study_block_done(block_id: int, data: dict, request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        block = set_study_block_done(user_id, block_id, bool(data.get("done")))
        if not block:
            raise HTTPException(status_code=404, detail="Nie znaleziono bloku nauki.")
        return block
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd aktualizacji bloku: {error}") from error


@app.get("/stats")
def stats(
    request: Request,
    response: Response,
    start_date: str | None = None,
    end_date: str | None = None,
    plan_id: int | None = None,
):
    user_id = get_current_user_id(request, response)
    validate_date_range(start_date, end_date)

    try:
        return get_study_stats(user_id, start_date, end_date, plan_id)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd liczenia statystyk: {error}") from error


@app.post("/plan-study")
def plan_study(data: dict, request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        block_name = data.get("block_name") or "Nauka"
        total_hours = int(data.get("total_hours"))
        deadline_str = data.get("deadline_str")
        study_location = str(data.get("study_location") or "").strip()
        participant_user_ids = normalize_participant_user_ids(user_id, data.get("participant_user_ids") or [user_id])

        plan = generate_study_plan_for_users(
            owner_user_id=user_id,
            participant_user_ids=participant_user_ids,
            total_hours=total_hours,
            deadline_str=deadline_str,
            location=study_location
        )
        return save_study_blocks(
            user_id,
            plan,
            block_name,
            total_hours,
            deadline_str,
            participant_user_ids,
            study_location=study_location,
        )
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd generowania planu: {error}") from error


@app.post("/plan-study/push")
def plan_study_and_push(data: dict, request: Request, response: Response):
    user_id = get_current_user_id(request, response)

    try:
        block_name = data.get("block_name") or "Nauka"
        total_hours = int(data.get("total_hours"))
        deadline_str = data.get("deadline_str")
        study_location = str(data.get("study_location") or "").strip()
        participant_user_ids = normalize_participant_user_ids(user_id, data.get("participant_user_ids") or [user_id])

        plan = generate_study_plan_for_users(
            owner_user_id=user_id,
            participant_user_ids=participant_user_ids,
            total_hours=total_hours,
            deadline_str=deadline_str,
            location=study_location,
        )
        saved = save_study_blocks(
            user_id,
            plan,
            block_name,
            total_hours,
            deadline_str,
            participant_user_ids,
            study_location=study_location,
        )
        plan_id = saved["plan"]["id"]
        result = save_blocks_to_google(user_id, saved["blocks"])
        refreshed_blocks = get_study_blocks_by_plan(user_id, plan_id)

        refreshed_plan = sync_plan_google_state(user_id, plan_id)["plan"]

        return {
            "message": "Plan został przetworzony.",
            "pushed_count": len(result["pushed"]),
            "skipped_count": len(result["skipped"]),
            "plan": refreshed_plan,
            "blocks": refreshed_blocks,
        }
    except HTTPException:
        raise
    except RuntimeError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Błąd wysyłania planu do Google: {error}",
        ) from error


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main_google_api:app", host="127.0.0.1", port=8000, reload=True)
