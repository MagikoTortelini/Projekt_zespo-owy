from datetime import datetime
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from Google_auth import user_credentials

# Przechowywanie tokenu do synchronizacji w pamięci aplikacji
sync_token_store = {}

# Uproszczenie danych otrzmwanych z googla do dalszych operacji
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

# Tworzenie credentials użytkownika
def build_credentials():
    if "creds" not in user_credentials:
        raise RuntimeError("Użytkownik nie jest zalogowany do Google Calendar.")

    creds_data = user_credentials["creds"]

    return Credentials(
        token=creds_data["token"],
        refresh_token=creds_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"],
    )

# Stworzenie serwisu google calendar
def get_calendar_service():
    creds = build_credentials()
    return build("calendar", "v3", credentials=creds)

# Synchronizacja wydarzeń
# piersze wyowłanie zwraca wszystkie wydarzenie następne zmienione
def sync_events():

    service = get_calendar_service()
    sync_token = sync_token_store.get("token")

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
            sync_token_store["token"] = None
            return sync_events()
        raise

    events = event_result.get("items", [])
    sync_token_store["token"] = event_result.get("nextSyncToken")

    return {
        "sync_type": sync_type,
        "count": len(events),
        "events": [simplify_event(event) for event in events],
    }

# Zwraca wszystkie aktualne eventy
def get_events():
    service = get_calendar_service()
    now = datetime.now(ZoneInfo("Europe/Warsaw")).isoformat()

    event_result = service.events().list(
        calendarId="primary",
        singleEvents=True,
        orderBy="startTime",
        timeMin=now,
    ).execute()

    events = event_result.get("items", [])
    return [simplify_event(event) for event in events]

#Dodanie wydarzeń do kalendarza
def add_event(start, end, summary, localization):
    service = get_calendar_service()

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

# Reset tokenu synchronizacji
def reset_sync_token():
    sync_token_store["token"] = None