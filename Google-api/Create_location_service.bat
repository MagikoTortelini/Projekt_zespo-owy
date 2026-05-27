@echo off
setlocal enabledelayedexpansion

echo ==========================================
echo  SCALONY SETUP: OSRM + NOMINATIM
echo ==========================================

echo [1/4] Usuwanie starych kontenerów (jesli istnieja)...
docker rm -f nominatim_DS 2>nul
docker rm -f osrm_server 2>nul

start
echo [2/4] Startowanie kontenera Nominatim w nowym oknie...
start "SERWER: Nominatim" docker run -it ^
  --shm-size=2g ^
  -e PBF_URL=https://download.geofabrik.de/europe/poland/dolnoslaskie-latest.osm.pbf ^
  -e NOMINATIM_THREADS=4 ^
  -p 8080:8080 ^
  -v nominatim-data:/var/lib/postgresql/16/main ^
  --name nominatim_DS ^
  mediagis/nominatim:5.1

echo [3/4] Przygotowywanie danych OSRM (to moze potrwac)...

echo -> Extracting...
docker run -t -v "%cd%\docker\data:/data" osrm/osrm-backend osrm-extract -p /opt/foot.lua /data/poland-latest.osm.pbf

echo -> Partitioning...
docker run -t -v "%cd%\docker\data:/data" osrm/osrm-backend osrm-partition /data/poland-latest.osrm

echo -> Customizing...
docker run -t -v "%cd%\docker\data:/data" osrm/osrm-backend osrm-customize /data/poland-latest.osrm

echo [4/4] Startowanie serwera OSRM w nowym oknie...
start "SERWER: OSRM Routed" docker run -t -i ^
  -p 5000:5000 ^
  --name osrm_server ^
  -v "%cd%\docker\data:/data" ^
  osrm/osrm-backend ^
  osrm-routed --algorithm mld /data/poland-latest.osrm

echo.
echo ==========================================
echo  WSZYSTKIE PROCESY URUCHOMIONE
echo  - Nominatim: http://localhost:8080
echo  - OSRM: http://localhost:5000
echo.
echo  Sprawdz logi w nowych oknach terminala.
echo ==========================================
pause