@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [エラー] 仮想環境がありません。先に setup.bat を実行してください。
    pause
    exit /b 1
)

REM 引数はそのまま raspi4eyes.py へ渡す (例: run.bat --windowed --debug-noise)
".venv\Scripts\python.exe" raspi4eyes.py %*

pause
