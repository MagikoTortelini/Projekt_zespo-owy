from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from Google_auth import user_credentials
from datetime import datetime
from zoneinfo import ZoneInfo

#Przechowanie tokena do synchronizacji
sync_token_store={}

#Stworzenie serwisu kalendarza
def get_calendar_service():
    if "creds" not in user_credentials:
        raise Exception("Użytkownik nie zautoryzowany")

    creds_data=user_credentials["creds"]

    creds=Credentials(
        token=creds_data["token"],
        refresh_token=creds_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"]
    )

    return build("calendar","v3",credentials=creds)

#Synchronizacja(Pierwsze wywyołanie zwraca wszystkie wydarzenia, każde następne zmiany)
def sync_events():
    service=get_calendar_service()
    sync_token=sync_token_store.get("token")
    try:
        if not sync_token:
            event_result = service.events().list(
                calendarId="primary",
                singleEvents=True,
            ).execute()
        else:
            event_result = service.events().list(
                calendarId="primary",
                singleEvents=True,
                syncToken=sync_token
            ).execute()
    except Exception as e:
        if "410" in str(e):
            pass
            sync_token_store["token"]=None
            return sync_events()
        else:
            raise e
    events=event_result.get("items",[])
    sync_token_store["token"]=event_result.get("nextSyncToken") #Zapis tokenu do synchronizacji
    return events

#Zwraca wszystkie wydarzenia od teraz
def get_events():
    service=get_calendar_service()
    event_result=service.events().list(
        calendarId="primary", #Id kalendarza
        singleEvents=True, #Wyświetla wydarzenia cykliczne jako osobne wydarzenia
        orderBy="startTime", #Sortowanie po czasie startu wydarzenia
        timeMin=datetime.now(ZoneInfo("Europe/Warsaw")).isoformat() #Branie tylko wydarzeń które zaczynają się później niż teraz
    ).execute()

    return event_result.get("items",[])

#Dodanie wydarzenia
def add_event(start,end,summary,localization):
    service = get_calendar_service()
    event={
        "summary":summary, #Nazwa wydarzenia
        "location":localization, #Lokalizacja wydarzenia
        "start":{
            "dateTime":start, #Czas startu wydarzenia
            "timeZone":"Europe/Warsaw",
        },
        "end":{
            "dateTime": end, #Czas końca wydarzenia
            "timeZone": "Europe/Warsaw",
        },
    }

    #Dodanie wydarzenia do kalędarza
    new_event=service.events().insert(
        calendarId="primary",
        body=event
    ).execute()
