# Study Planner - Prototyp

## Co robi ta wersja
- loguje do Google Calendar,
- pobiera wydarzenia z Google,
- zapisuje je lokalnie do SQLite,
- generuje plan na podstawie danych z bazy,
- wysyła plan do Google Calendar.

## Baza danych
Po pierwszym uruchomieniu tworzy się plik:
- `study_planner.db`

Tabela:
- `calendar_events`
- `user_preferences`

## Jak uruchomić
1. Wejdź do folderu `Google-api`
2. Uruchom:
   - PowerShell: `./start_project.bat`
   - albo ręcznie:
     - `python -m venv .venv`
     - `./.venv/Scripts/python.exe -m pip install -r requirements.txt`
     - `./.venv/Scripts/python.exe main_google_api.py`
3. Otwórz `http://127.0.0.1:8000/`
4. Kliknij:
   - `Zaloguj Google`
   - `Importuj eventy do bazy`
   - `Pokaż eventy z bazy`
   - `Generuj plan z bazy`
   - `Wyślij plan do Google`
