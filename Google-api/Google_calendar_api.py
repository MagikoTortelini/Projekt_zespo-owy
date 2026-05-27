import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from db import get_google_credentials, save_google_credentials

load_dotenv()

sync_token_store = {}


DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"


def simplify_event(event):
    start = event.get("start", {})
    end = event.get("end", {})

    return {
        "id": event.get("id"),
        "summary": event.get("summary"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "location": event.get("location", ""),
        "status": event.get("status", ""),
        "htmlLink": event.get("htmlLink", ""),
    }


def split_scopes(scopes: str | None):
    return [scope for scope in (scopes or "").split() if scope]


def credentials_to_dict(creds):
    expiry = None
    if getattr(creds, "expiry", None):
        expiry = creds.expiry.isoformat()

    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri or DEFAULT_TOKEN_URI,
        "scopes": " ".join(creds.scopes or []),
        "expiry": expiry,
    }


def build_credentials(user_id: int):
    creds_data = get_google_credentials(user_id)

    if not creds_data:
        raise RuntimeError("Użytkownik nie jest zalogowany do Google Calendar.")

    expiry = None
    if creds_data.get("expiry"):
        try:
            expiry = datetime.fromisoformat(creds_data["expiry"])
        except ValueError:
            expiry = None

    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data.get("token_uri") or DEFAULT_TOKEN_URI,
        client_id=os.getenv("CLIENT_ID"),
        client_secret=os.getenv("CLIENT_SECRET"),
        scopes=split_scopes(creds_data.get("scopes")),
        expiry=expiry,
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
        save_google_credentials(user_id, credentials_to_dict(creds))

    return creds


def get_calendar_service(user_id: int):
    creds = build_credentials(user_id)
    return build("calendar", "v3", credentials=creds)


def sync_events(user_id: int):
    service = get_calendar_service(user_id)
    sync_token = sync_token_store.get(user_id)

    try:
        if not sync_token:
            event_result = service.events().list(
                calendarId="primary",
                singleEvents=True,
                orderBy="updated",
            ).execute()
            sync_type = "full"
        else:
            event_result = service.events().list(
                calendarId="primary",
                singleEvents=True,
                syncToken=sync_token,
            ).execute()
            sync_type = "delta"
    except Exception as error:
        if "410" in str(error):
            sync_token_store[user_id] = None
            return sync_events(user_id)
        raise

    events = event_result.get("items", [])
    sync_token_store[user_id] = event_result.get("nextSyncToken")

    return {
        "sync_type": sync_type,
        "count": len(events),
        "events": [simplify_event(event) for event in events],
    }


def get_events(user_id: int):
    service = get_calendar_service(user_id)
    now = datetime.now(ZoneInfo("Europe/Warsaw")).isoformat()

    event_result = service.events().list(
        calendarId="primary",
        singleEvents=True,
        orderBy="startTime",
        timeMin=now,
    ).execute()

    events = event_result.get("items", [])
    return [simplify_event(event) for event in events]


def add_event(user_id: int, start, end, summary, localization):
    service = get_calendar_service(user_id)

    event = {
        "summary": summary,
        "location": localization,
        "start": {
            "dateTime": start,
            "timeZone": "Europe/Warsaw",
        },
        "end": {
            "dateTime": end,
            "timeZone": "Europe/Warsaw",
        },
    }

    new_event = service.events().insert(
        calendarId="primary",
        body=event,
    ).execute()

    return simplify_event(new_event)


def reset_sync_token(user_id: int):
    sync_token_store[user_id] = None

def google_event_exists(user_id: int, event_id: str | None) -> bool:
    if not event_id:
        return False

    service = get_calendar_service(user_id)

    try:
        event = service.events().get(
            calendarId="primary",
            eventId=event_id,
        ).execute()
    except HttpError as error:
        if error.resp.status in (404, 410):
            return False
        raise

    return event.get("status") != "cancelled"


def delete_google_event(user_id: int, event_id: str | None) -> bool:
    if not event_id:
        return False

    service = get_calendar_service(user_id)

    try:
        service.events().delete(
            calendarId="primary",
            eventId=event_id,
        ).execute()
        return True
    except HttpError as error:
        if error.resp.status in (404, 410):
            return False
        raise

