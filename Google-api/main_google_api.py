from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from Google_auth import clear_credentials, generate_auth_url, handle_callback, is_authenticated
from Google_calendar_api import get_events, reset_sync_token, sync_events
from Study_planner import format_study_plan, generate_study_plan_from_saved_events, save_plan_to_google
from db import clear_calendar_events, get_calendar_events, get_user_preferences, init_db, insert_imported_events, save_user_preferences

app = FastAPI(title="Study Planner", version="1.0.0")

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "templates" / "index.html"

# Inicjalizacja bazy danych
@app.on_event("startup")
def startup():
    init_db()

# Strona główna
@app.get("/")
def home():
    return FileResponse(INDEX_FILE)

# Zwrot statusy zalogowania
@app.get("/status")
def status():
    return {"authenticated": is_authenticated()}

# Odczyt preferencji z bazy danych
@app.get("/preferences")
def preferences():
    try:
        return get_user_preferences()
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd odczytu preferencji: {error}") from error

# Zapisanie preferencji w bazie
@app.post("/preferences")
def update_preferences(data: dict):
    try:
        save_user_preferences(data)
        return {
            "message": "Preferencje zapisane poprawnie.",
            "preferences": get_user_preferences(),
        }
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd zapisu preferencji: {error}") from error

# Zalogowanie
@app.get("/auth/login")
def login():
    auth_url = generate_auth_url()
    return RedirectResponse(auth_url)

# Wylogowanie
@app.get("/auth/logout")
def logout():
    clear_credentials()
    reset_sync_token()
    return RedirectResponse(url="/?logout=success")

#Callback po autoryzacji
@app.get("/auth/google/callback")
def callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    try:
        handle_callback(code, state)
        return RedirectResponse(url="/?login=success")
    except Exception as error:
        return RedirectResponse(url=f"/?login=error&details={str(error)}")

#Synchronizacja
@app.get("/sync")
def sync():
    try:
        return sync_events()
    except RuntimeError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd synchronizacji: {error}") from error

# Import wydarzeń do bazy
@app.get("/events/import")
def import_events_to_db():
    try:
        events = get_events()

        clear_calendar_events()
        insert_imported_events(events)

        saved_events = get_calendar_events()

        return {
            "message": "Zaimportowano wydarzenia do bazy.",
            "count": len(saved_events),
            "events": saved_events,
        }
    except RuntimeError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd importu do bazy: {error}") from error

# Odczyt wydarzeń z bazy
@app.get("/events")
def events():
    try:
        return get_calendar_events()
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd odczytu wydarzeń z bazy: {error}") from error

# Generowanie planu
@app.post("/plan-study")
def plan_study(data: dict):
    try:
        plan = generate_study_plan_from_saved_events(
            total_hours=int(data.get("total_hours")),
            deadline_str=data.get("deadline_str"),
        )

        return format_study_plan(plan)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Błąd generowania planu: {error}") from error

# Wysłanie planu do kalendarza
@app.post("/plan-study/push")
def plan_study_and_push(data: dict):
    try:
        plan = generate_study_plan_from_saved_events(
            total_hours=int(data.get("total_hours")),
            deadline_str=data.get("deadline_str"),
        )

        result = save_plan_to_google(plan)

        return {
            "message": "Plan został przetworzony.",
            "pushed_count": len(result["pushed"]),
            "skipped_count": len(result["skipped"]),
            "plan": format_study_plan(plan),
        }
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Błąd wysyłania planu do Google: {error}",
        ) from error


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main_google_api:app", host="127.0.0.1", port=8000, reload=True)