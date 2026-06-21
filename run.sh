#!/bin/bash

# 1. バックグラウンドでCageの起動完了（2秒）を待ってから、解像度を変更する処理
(
  sleep 2
  # 両方のポートに対して解像度設定を試みる（接続されている方だけ適用されます）
  WAYLAND_DISPLAY=wayland-0 wlr-randr --output HDMI-A-1 --mode 1280x960@60 2>/dev/null
  WAYLAND_DISPLAY=wayland-0 wlr-randr --output HDMI-A-2 --mode 1280x960@60 2>/dev/null
) &

# 2. Pythonプログラムを実行 (仮想環境のPythonを呼び出す)
# スクリプトの置いてあるディレクトリに移動
cd "$(dirname "$0")"
.venv/bin/python raspi4eyes.py --fullscreen
