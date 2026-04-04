from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from datetime import datetime
from zoneinfo import ZoneInfo
from Google_auth import generate_auth_url,handle_callback
from Google_calendar_api import get_events,add_event,sync_events

app = FastAPI()

#Logowanie za pomoc googla i autoryzacja
@app.get("/auth/login")
def login():
    auth_url = generate_auth_url()
    return RedirectResponse(auth_url)

#Callback po autoryzacji
@app.get("/auth/google/callback")
def callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    handle_callback(code,state)
    return {"message": "Zalogowano!"}

#Synchronizacja
@app.get("/sync")
def sync():
    return sync_events()

#Dodanie wydarzenia przyjmuje w konsoli
#Date startu w formacie dd-mm-rrrr hh:mm
#Date końca w formacie dd-mm-rrrr hh:mm
#Nazwe wydarzenia string
#Lokalizację string
@app.get("/add")
def add():
    #Testowe dodawanie wydarzeń
    start = input()
    end = input()
    summary = input()
    localization = input()
    add_event(parse_time(start), parse_time(end), summary, localization)
    return {"message":f"Dodanao {summary} w {localization} od {parse_time(start)} do {parse_time(end)}"}

#Wyświetlenie wszystkich wydarzeń użytkownika od teraz
@app.get("/events")
def events():
    return get_events()

#Zamiana formatu daty na akceptowalny przez google razem z strefą czasową
def parse_time(date):
    dt = datetime.strptime(date, "%d-%m-%Y %H:%M")
    dt = dt.replace(tzinfo=ZoneInfo("Europe/Warsaw"))
    return dt.isoformat()
