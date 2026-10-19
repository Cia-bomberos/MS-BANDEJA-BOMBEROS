@echo off
docker start pg-test >nul 2>&1 || docker run --name pg-test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=bandeja_test -p 5433:5432 -d postgres:16
timeout /t 5 >nul
set TEST_DB_HOST=localhost
set TEST_DB_PORT=5433
set TEST_DB_NAME=bandeja_test
set TEST_DB_USER=postgres
set TEST_DB_PASSWORD=test
pytest -m integration -v %*