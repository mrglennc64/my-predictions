@echo off
cd /d C:\dev\contest-edge
if not exist logs mkdir logs
python -m uvicorn app.api:app --port 8600 >> logs\server.log 2>&1
