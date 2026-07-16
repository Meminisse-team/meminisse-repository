@echo off
setlocal
set "ROOT=%~dp0"

echo ============================================
echo  Meminisse Dev Environment
echo ============================================

echo [1/3] Starting Docker (redis + worker + beat)...
echo       (rebuilds only if something changed - cached builds are fast)
pushd "%ROOT%backend"
docker compose up -d --build
popd

echo [2/3] Starting backend (FastAPI) in a new window...
start "Meminisse Backend" "%ROOT%backend\run-backend.bat"

echo [3/3] Starting frontend (Next.js) in a new window...
start "Meminisse Frontend" "%ROOT%frontend\run-frontend.bat"

echo.
echo Done. Backend and frontend are running in their own windows.
echo Docker (redis/worker/beat) runs in the background - check with: docker compose ps
echo You can close this window now.
pause
