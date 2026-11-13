@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 正在建立 Python 虛擬環境並安裝 Flask...
  py -3 -m venv .venv 2>nul
  if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
  )
  if not exist ".venv\Scripts\python.exe" goto :no_python
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

start "" http://127.0.0.1:8000/
".venv\Scripts\python.exe" app.py %*
goto :end

:no_python
echo.
echo 找不到 Python，請先安裝 Python 3.10 以上版本。
pause

:end
pause
