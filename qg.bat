@echo off
rem Wrapper to run quotegif via Docker Compose on Windows.
rem
rem Usage (from the project directory):
rem   qg find "no soup for you"
rem   qg compare "that's what she said" --providers openai,ollama
rem   qg index
rem   qg config
rem
rem To use it from anywhere, add this directory to your PATH or copy qg.bat
rem somewhere already on PATH.

docker compose -f "%~dp0docker-compose.yml" run --rm quotegif %*
