@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo === raspi4eyes セットアップ ===
echo.

REM --- 仮想環境(.venv)の作成 ---
if not exist ".venv\Scripts\python.exe" (
    echo 仮想環境を作成しています...
    python -m venv .venv
    if errorlevel 1 (
        echo python コマンドが見つかりません。py ランチャーで再試行します...
        py -3 -m venv .venv
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo [エラー] 仮想環境の作成に失敗しました。Python のインストールと PATH を確認してください。
    pause
    exit /b 1
)

REM --- pip 更新 ---
echo pip を更新しています...
".venv\Scripts\python.exe" -m pip install --upgrade pip

REM --- 依存パッケージのインストール ---
echo 依存パッケージをインストールしています...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [エラー] パッケージのインストールに失敗しました。
    pause
    exit /b 1
)

echo.
echo === セットアップ完了。run.bat で起動できます。 ===
pause
