@echo off
rem GPU wrapper — uses quotegif-gpu service (CUDA Whisper).
docker compose -f "%~dp0docker-compose.yml" --profile gpu run --rm quotegif-gpu %*
