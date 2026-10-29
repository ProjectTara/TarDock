@echo off
REM Build lokal (opsional). Rilis resmi dibangun GitHub Actions.
cd /d "%~dp0"
set "PY=%USERPROFILE%\python312\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m pip install -q -r requirements.txt -r requirements-build.txt || goto :err
set QT_QPA_PLATFORM=offscreen
"%PY%" taradock.py --selftest selftest-source.json || goto :err
set QT_QPA_PLATFORM=
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name TaraDock --icon assets/taradock.ico --add-data "assets/taradock.ico;assets" --exclude-module tkinter taradock.py || goto :err
echo.
echo Selesai: %CD%\dist\TaraDock.exe
explorer dist
pause
exit /b 0
:err
echo Build gagal. Screenshot pesan di atas.
pause
exit /b 1
