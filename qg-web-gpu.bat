@echo off
rem Start/manage QuoteGif web UI (GPU) via Docker Compose.
docker compose -f "%~dp0docker-compose.yml" --profile web --profile gpu %*
