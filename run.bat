@echo off
start "SmartDOC Frontend" cmd /k "cd frontend && npm run dev"
backend\myvenv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
