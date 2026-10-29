@echo off
REM Jalankan TaraDock dari source (untuk PC yang memblokir exe).
REM Memakai Python portable di %USERPROFILE%\python312 jika ada.
cd /d "%~dp0"
set "PY=%USERPROFILE%\python312\python.exe"
set "PYW=%USERPROFILE%\python312\pythonw.exe"
if not exist "%PY%" (
  set "PY=python"
  set "PYW=pythonw"
)
"%PY%" -c "import PySide6" 2>nul || (
  echo Install PySide6...
  "%PY%" -m pip install -r requirements.txt || goto :err
)
start "" "%PYW%" "%~dp0taradock.py"
exit /b 0
:err
echo Gagal install library. Screenshot pesan di atas.
pause
exit /b 1
