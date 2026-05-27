import json
import os

import requests
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

from db import (
    delete_google_credentials,
    get_or_create_google_user,
    has_google_credentials,
    save_google_credentials,
)

load_dotenv()


os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").strip().rstrip("/")
REDIRECT_URI = f"{APP_BASE_URL}/auth/google/callback"

USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def parse_redirect_uris():
    raw = os.getenv("REDIRECT_URIS", "")
    redirect_uris = []

    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                redirect_uris = [str(item) for item in parsed]
        except json.JSONDecodeError:
            redirect_uris = [item.strip() for item in raw.split(",") if item.strip()]

    if REDIRECT_URI not in redirect_uris:
        redirect_uris.append(REDIRECT_URI)

    return redirect_uris


def parse_scopes():
    raw = os.getenv("SCOPES", "https://www.googleapis.com/auth/calendar")

    aliases = {
        "email": "https://www.googleapis.com/auth/userinfo.email",
        "profile": "https://www.googleapis.com/auth/userinfo.profile",
    }

    scopes = []

    for part in raw.replace(",", " ").split():
        value = part.strip()
        if not value:
            continue

        value = aliases.get(value, value)

        if value not in scopes:
            scopes.append(value)

    required_scopes = [
        "https://www.googleapis.com/auth/calendar",
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]

    for required_scope in required_scopes:
        if required_scope not in scopes:
            scopes.append(required_scope)

    return scopes


SCOPES = parse_scopes()

CLIENT_CONFIG = {
    "web": {
        "client_id": os.getenv("CLIENT_ID"),
        "client_secret": os.getenv("CLIENT_SECRET"),
        "redirect_uris": parse_redirect_uris(),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

flow_store = {}


def create_auth_flow():
    return Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )


def credentials_to_dict(creds):
    expiry = None
    if getattr(creds, "expiry", None):
        expiry = creds.expiry.isoformat()

    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "scopes": " ".join(creds.scopes or SCOPES),
        "expiry": expiry,
    }


def fetch_google_profile(creds):
    response = requests.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=10,
    )

    if response.status_code != 200:
        raise ValueError(
            "Nie udało się pobrać danych użytkownika Google. "
            "Sprawdź, czy w SCOPES są: openid email profile."
        )

    data = response.json()

    return {
        "google_sub": data.get("sub"),
        "email": data.get("email"),
        "display_name": data.get("name") or data.get("email") or "Użytkownik Google",
    }


def generate_auth_url():
    flow = create_auth_flow()

    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="false",
    )

    flow_store[state] = {
        "flow": flow,
    }

    return auth_url


def handle_callback(code, state):
    if not code or not state:
        raise ValueError("Brak kodu autoryzacji lub state")

    stored_flow = flow_store.pop(state, None)
    if not stored_flow:
        raise ValueError("Invalid state")

    flow = stored_flow["flow"]
    flow.fetch_token(code=code)
    creds = flow.credentials

    profile = fetch_google_profile(creds)
    user_id = get_or_create_google_user(
        google_sub=profile.get("google_sub"),
        email=profile.get("email"),
        display_name=profile.get("display_name"),
    )

    save_google_credentials(user_id, credentials_to_dict(creds))

    return user_id


def is_authenticated(user_id: int):
    return has_google_credentials(user_id)


def clear_credentials(user_id: int):
    delete_google_credentials(user_id)
