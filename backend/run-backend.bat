@echo off
cd /d "%~dp0"
"%~dp0..\.venv\Scripts\fastapi.exe" dev app/main.py
