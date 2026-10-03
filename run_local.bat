@echo off
rem Local end-to-end test: server + website on http://127.0.0.1:8000 and the logger sending to it.
rem Catches go to .local\bitemap.db (delete it to start over). Your normal logger settings are not changed.
cd /d "%~dp0"
if not exist .local mkdir .local
set DATABASE_URL=sqlite:///%~dp0.local\bitemap.db
start "BiteMap server" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --app-dir server --host 127.0.0.1 --port 8000"
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8000/
set BITEMAP_SERVER=http://127.0.0.1:8000
cd client
start "BiteMap Logger" ..\.venv\Scripts\python.exe -m bitemap_logger
