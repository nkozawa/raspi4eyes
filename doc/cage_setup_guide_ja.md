# Cageを使用したヘッドレス環境での起動手順 (日本語)

このドキュメントでは、X11やWaylandデスクトップ環境がインストールされていないLinuxシステム（Raspberry Pi OS Liteなど）で、`Cage`コンポジタを使用して `raspi4eyes` をHDMIへ直接出力するための設定手順を説明します。

---

## 1. 必要パッケージのインストール

まず、超軽量なWaylandコンポジタである `Cage`、ディスプレイ設定管理ツール `wlr-randr`、およびセッション管理者 `seatd` をインストールします。

```bash
sudo apt update
sudo apt install -y cage xwayland wlr-randr seatd
```

---

## 2. 権限の設定（ユーザーグループの追加）

SSH接続などの非ログイン端末から物理グラフィックスデバイスやセッションを制御できるように、実行ユーザーに必要な権限を付与します。

1. 以下のコマンドを実行し、ユーザーを必要なグループに追加します。
   ```bash
   sudo usermod -aG video,render,seat $USER
   ```
2. `seatd` サービスをバックグラウンドで常時起動するように設定します。
   ```bash
   sudo systemctl enable --now seatd
   ```
3. **【重要】設定を反映するため、一度SSH接続を切断して再接続（ログアウト ➔ 再ログイン）してください。**
   ```bash
   exit
   ```

---

## 3. Python仮想環境 (venv) の構築
Raspberry Pi OSのシステム環境のパッケージと競合するのを防ぐため、Pythonの仮想環境（`venv`）を構築して実行することを強く推奨します。

```bash
# リポジトリのディレクトリへ移動
cd ~/rasp4eyes

# '.venv' という名前で仮想環境を作成
python3 -m venv .venv

# 仮想環境を有効化
source .venv/bin/activate
```

---

## 4. 依存パッケージのインストール
仮想環境が有効な状態で、必要な依存パッケージ（`opencv-python`, `numpy`）をインストールします。

```bash
pip install -r requirements.txt
```


---

## 5. 起動スクリプトの準備

`cmdline.txt` による解像度変更は、HDMI接続の安定性に影響を与える可能性があるため、OS側はモニター推奨の解像度（1920x1080 など）を維持し、プログラム側で描画サイズを拡大して出力する方式を採用しています。これにより、HDMIの抜き差し時でも解像度が変わらず、非常に安定してフルスクリーン表示を維持できます。

### run.sh の内容
すでに `run.sh` は作成済みですが、内容は以下のようになっています。

```bash
#!/bin/bash

# Pythonプログラムを実行 (仮想環境のPythonを呼び出す)
cd "$(dirname "$0")"
.venv/bin/python raspi4eyes.py --fullscreen
```

*実行権限が付与されていることを確認してください:*
```bash
chmod +x run.sh
```

---

## 6. プログラムの実行（手動実行）

準備が整ったら、以下のコマンドで起動します。

```bash
# seatdサービスが動作しているため、直接cageを呼び出します
cage -- ./run.sh
```

画面出力をエラーログで汚したくない場合は、以下の環境変数を追加して起動します（推奨）。
```bash
WLR_LOG_LEVEL=error cage -- ./run.sh
```

---

## 7. OS起動時の自動起動設定（systemdサービス化）

OSの起動時に自動的に `Cage` と `raspi4eyes` を立ち上げるには、`systemd` を使用します。

### 1. サービスファイルの作成
`/etc/systemd/system/raspi4eyes.service` を新規作成します。
```bash
sudo nano /etc/systemd/system/raspi4eyes.service
```

以下の内容を書き込みます（ユーザー名 `User` やディレクトリ `WorkingDirectory` は環境に合わせて書き換えてください）。

```ini
[Unit]
Description=raspi4eyes display service using Cage
After=network.target systemd-logind.service seatd.service
Wants=seatd.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/raspi4eyes
# PAMセッションを開始し、XDG_RUNTIME_DIRなどの環境変数を自動生成させる
PAMName=login
ExecStart=/usr/bin/cage -s -- /home/pi/raspi4eyes/run.sh
Environment=WLR_LOG_LEVEL=error
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> [!IMPORTANT]
> `PAMName=login` を指定しない場合、Waylandの動作に必要な `XDG_RUNTIME_DIR` 環境変数がセットアップされず、`XDG_RUNTIME_DIR is not set in the environment` エラーで起動に失敗します。必ず記述してください。

### 2. サービスの有効化と起動
作成したサービスをシステムに登録し、起動します。

```bash
# 設定をリロード
sudo systemctl daemon-reload

# 自動起動を有効化
sudo systemctl enable raspi4eyes.service

# 今すぐ起動テストを行う
sudo systemctl start raspi4eyes.service
```

### 3. ステータスとログの確認
正しく動いているか、またはエラーが出ていないかを確認します。

```bash
# 稼働ステータスの確認
sudo systemctl status raspi4eyes.service

# リアルタイムログの表示
journalctl -u raspi4eyes.service -f
```

---

## 8. トラブルシューティング

### Q1: `Could not open target tty: Permission denied` というエラーが出る
* **原因**: ユーザーグループの追加設定が現在のセッションに反映されていないか、権限がまだ不足しています。
* **対策**: 一度ログアウトして再ログインしたか確認してください。それでも解決しない場合は、ユーザーを `tty` グループに追加して再度ログインを試してください。
  ```bash
  sudo usermod -aG tty $USER
  ```

### Q2: `refusing to start / seatd exited prematurely` というエラーが出る
* **原因**: `seatd-launch cage ...` で起動しようとした際、すでにバックグラウンドで `seatd` サービスが起動しているために発生します。
* **対策**: `seatd-launch` コマンドを使用せず、単に `cage -- ./run.sh` と実行してください。
