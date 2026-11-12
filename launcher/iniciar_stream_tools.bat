@echo off
REM ==========================================================
REM  Inicia Scout y los dos trackers, cada uno en su consola.
REM  Todas las credenciales salen de launcher\.env.
REM ==========================================================
set "RAIZ=%~dp0.."

start "Scout" cmd /k "cd /d "%RAIZ%\scout" && call .venv\Scripts\activate && python main.py"
start "Tracker Ingame" cmd /k "cd /d "%RAIZ%\trackers\scripts" && python tracker_ingame.py"
start "Tracker Rotativo" cmd /k "cd /d "%RAIZ%\trackers\scripts" && python tracker_rotativo.py"

exit
