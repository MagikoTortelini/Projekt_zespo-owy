from geopy.geocoders import Nominatim
import requests

# Publiczne API Nominatim (OpenStreetMap) – nie wymaga lokalnego serwera.
# Zadbaj o unikalny user_agent zgodnie z polityką OSM.



def Distance_and_tiem(Start_address, End_address):
    test=False
    if test==False:
        geolocator = Nominatim(
            user_agent="Study_planner",
            domain="localhost:8080",
            scheme="http"
        )
        start_location = geolocator.geocode(Start_address)
        end_location = geolocator.geocode(End_address)

        if start_location is None:
            raise ValueError(f"Nie znaleziono lokalizacji: {Start_address!r}")
        if end_location is None:
            raise ValueError(f"Nie znaleziono lokalizacji: {End_address!r}")

        url = f"http://localhost:5000/route/v1/foot/{start_location.longitude},{start_location.latitude};{end_location.longitude},{end_location.latitude}"

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        distance = round(data["routes"][0]["distance"])       # metry
        duration = round(data["routes"][0]["duration"])       # sekundy
        return (distance, duration)
    return (500,900)


if __name__ == "__main__":
    # Testy manualne – uruchamiaj bezpośrednio: python Map_distance_API.py
    distance, travel_seconds = Distance_and_tiem("Kazimierza Wielkiego 35, 50-077 Wrocław", "sobótka")
    travel_seconds = int(distance / 8.3)
    print(f"Dystans: {distance}m  Czas: {int(travel_seconds / 60)} min")