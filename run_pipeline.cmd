@echo off
cd /d C:\dev\contest-edge
if not exist logs mkdir logs
python -m app.run_pipeline >> logs\pipeline.log 2>&1
