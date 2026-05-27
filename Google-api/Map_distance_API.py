from geopy.geocoders import Nominatim
import requests

geolocator = Nominatim(
    user_agent="Study_planner",
    domain="localhost:8080",
    scheme="http"
)
def Distance_and_tiem(Start_address,End_address):

    start_location=geolocator.geocode(Start_address)
    end_location=geolocator.geocode(End_address)

    #Do testów bez serwera OSRM
    # url = f"http://router.project-osrm.org/route/v1/foot/{start_location.longitude},{start_location.latitude};{end_location.longitude},{end_location.latitude}"
    url = f"http://localhost:5000/route/v1/foot/{start_location.longitude},{start_location.latitude};{end_location.longitude},{end_location.latitude}"

    response = requests.get(url)

    data = response.json()
    return_data=[round(data['routes'][0]['distance']),round(data["routes"][0]["duration"])]

    #Zwraca odległośc w metrach oraz czas w sekundach
    return (return_data[0],return_data[1])



#Testy manualne
distance, travel_seconds=Distance_and_tiem("zatorska","kobierzyce")
travel_seconds=int((distance/8.3))
print(f"Dystans: {distance}m Czas:{int(travel_seconds/60)}min")