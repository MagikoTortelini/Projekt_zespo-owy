@echo off

echo ==========================================
echo  STARTING NOMINATIM + OSRM
echo ==========================================

docker start nominatim_DS
docker start osrm_server

echo Opening logs windows...

start "Nominatim Logs" cmd /k docker logs -f nominatim_DS
start "OSRM Logs" cmd /k docker logs -f osrm_server

echo.
echo ==========================================
echo  SERVERS RUNNING
echo ==========================================

pause