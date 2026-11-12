@echo off
REM ==========================================================
REM  Setup - ejecutar UNA SOLA VEZ
REM  Crea el .venv de scout e instala las dependencias de todo.
REM ==========================================================
cd /d "%~dp0..\scout"
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
call .venv\Scripts\deactivate.bat

cd /d "%~dp0.."
pip install -r trackers\requirements.txt

if not exist "%~dp0.env" copy "%~dp0.env.example" "%~dp0.env"
echo.
echo Listo. Completa launcher\.env y despues usa iniciar_stream_tools.bat
pause
