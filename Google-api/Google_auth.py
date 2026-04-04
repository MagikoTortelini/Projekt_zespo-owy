from google_auth_oauthlib.flow import Flow
from dotenv import load_dotenv
import os

#Parametry do autoryzacji
load_dotenv()
CLIENT_CONFIG={
    "web":{
        "client_id": os.getenv("CLIENT_ID"),
        "client_secret": os.getenv("CLIENT_SECRET"),
        "redirect_uris": os.getenv("REDIRECT_URIS"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"
    }
}
#Tymczasowe przechowanie danych tokenu użytkownika i
flow_store = {}
user_credentials = {}

#Stworzenie autoryzacji googla
def create_auth_flow():
    return Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=["https://www.googleapis.com/auth/calendar"],
        redirect_uri=os.getenv("REDIRECT_URI")
    )

#Generowanie linku autoryzacyjnego
def generate_auth_url():
    flow = create_auth_flow()

    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent"
    )

    flow_store[state] = flow
    return auth_url

#Wymiana kodu autoryzacji na access i refresh token
def handle_callback(code, state):
    flow = flow_store.pop(state, None)
    if not flow:
        raise Exception("Invalid state")

    flow.fetch_token(code=code)
    creds = flow.credentials

    user_credentials["creds"] = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }

