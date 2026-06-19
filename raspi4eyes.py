#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
# OpenCVの内部警告ログ（VIDIOC_QBUF等のファイル記述子エラーなど）を抑制
os.environ["OPENCV_LOG_LEVEL"] = "ERROR"

import sys
import json
import time
import threading
import argparse
import numpy as np
import cv2

# デフォルト設定
DEFAULT_CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "devices": [
        "/dev/video0",
        "/dev/video2",
        "/dev/video4",
        "/dev/video6"
    ],
    "width": 640,
    "height": 480,
    "fps": 30,
    "fullscreen": True,
    "use_mjpeg": True,
    "show_no_signal_text": True,
    "detect_noise": True,
    "noise_threshold": 0.4
}

class CameraThread(threading.Thread):
    def __init__(self, device, width, height, fps, use_mjpeg, detect_noise, noise_threshold):
        super().__init__()
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.use_mjpeg = use_mjpeg
        self.detect_noise = detect_noise
        self.noise_threshold = noise_threshold
        self.frame = None
        self.running = True
        self.daemon = True
        self.current_noise_val = 0.0
        self.is_noise_active = False
        self.lock = threading.Lock()
        
    def run(self):
        cap = None
        last_retry = 0
        retry_interval = 3.0  # 秒
        consecutive_failures = 0
        max_consecutive_failures = 15  # 連続失敗許容回数（約0.5秒分）
        noise_counter = 0
        normal_counter = 0

        while self.running:
            if cap is None or not cap.isOpened():
                now = time.time()
                # 接続試行の間隔を確実に制限（切断されてから必ず3秒待つ）
                if now - last_retry > retry_interval:
                    last_retry = now
                    
                    # 数値ならintに変換
                    try:
                        dev_id = int(self.device)
                    except ValueError:
                        dev_id = self.device
                        
                    print(f"[{self.device}] 接続を試みています...")
                    cap = cv2.VideoCapture(dev_id)
                    if cap.isOpened():
                        # MJPEG設定 (Raspberry Piでの複数カメラ帯域不足対策に重要)
                        if self.use_mjpeg:
                            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                        
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                        cap.set(cv2.CAP_PROP_FPS, self.fps)
                        print(f"[{self.device}] 接続成功")
                        consecutive_failures = 0
                        noise_counter = 0
                        normal_counter = 0
                        self.is_noise_active = False
                    else:
                        cap = None
                        print(f"[{self.device}] 接続失敗")
                
                if cap is None:
                    time.sleep(0.5)
                    continue

            ret, frame = cap.read()
            if not ret:
                consecutive_failures += 1
                # 一時的なエラー（信号待ちなど）を許容し、連続失敗時のみ再接続へ
                if consecutive_failures >= max_consecutive_failures:
                    print(f"[{self.device}] フレームの読み込みに連続して失敗しました ({consecutive_failures}回)。再接続します。")
                    cap.release()
                    cap = None
                    with self.lock:
                        self.frame = None
                        self.current_noise_val = 0.0
                        self.is_noise_active = False
                    last_retry = time.time()  # 次回接続まで3秒のクールダウンを設定
                else:
                    time.sleep(0.03)  # 一時的失敗時は少し待ってループを回す
                continue
            
            # 読み込み成功時は失敗カウンターをリセット
            consecutive_failures = 0
            
            # 砂嵐（ノイズ）判定
            if self.detect_noise:
                is_noise = self.check_noise(frame)
                if is_noise:
                    noise_counter += 1
                    normal_counter = 0
                else:
                    normal_counter += 1
                    noise_counter = 0
                
                # チャタリング防止: 15フレーム連続でノイズならノイズ状態、5フレーム連続で正常なら正常状態
                if noise_counter >= 15:
                    self.is_noise_active = True
                elif normal_counter >= 5:
                    self.is_noise_active = False
            else:
                self.is_noise_active = False

            with self.lock:
                if self.is_noise_active:
                    self.frame = None
                else:
                    self.frame = frame
            
            # CPU負荷低減のための僅かなスリープ
            time.sleep(0.001)

        if cap is not None:
            cap.release()

    def check_noise(self, frame):
        """画像が砂嵐（ランダムノイズ）または無信号黒画面かどうかを判定する"""
        try:
            # 高速化のために小さく縮小して処理
            small = cv2.resize(frame, (80, 60))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            
            # 1. ぼかし前の標準偏差（生コントラスト）を計算
            _, stddev_raw = cv2.meanStdDev(gray)
            val_raw = stddev_raw[0][0]
            
            # 生のコントラストが極めて低い場合は、単一色（黒画面やブルーバック）とみなす
            if val_raw < 3.0:
                self.current_noise_val = 0.0
                return True
            
            # 2. 大きめのカーネルでガウシアンブラーをかけて細かいノイズ（砂嵐や走査線）を完全に平滑化
            blurred = cv2.GaussianBlur(gray, (15, 15), 0)
            
            # 3. ぼかし後の標準偏差（全体的な明暗のコントラスト）を計算
            _, stddev_blur = cv2.meanStdDev(blurred)
            val_blur = stddev_blur[0][0]
            
            # 4. コントラスト比（ぼかし後 / ぼかし前）を算出
            # 砂嵐：ぼかすと明暗差がほぼ消滅するため、比率は 0 に近くなる (例: 0.03)
            # 正常：ぼかしても大まかな構図（空と地面等）が残るため、比率は大きくなる (例: 0.60)
            ratio = val_blur / val_raw
            self.current_noise_val = ratio
            
            # コントラスト比が閾値以下（平坦化した割合が非常に大きい ＝ 砂嵐）ならノイズと判定
            return self.current_noise_val < self.noise_threshold
        except Exception:
            return False

    def get_frame(self):
        with self.lock:
            if self.frame is None:
                return None
            return self.frame.copy()

    def get_noise_value(self):
        """現在のノイズ判定値を取得する"""
        return self.current_noise_val

    def stop(self):
        self.running = False


def load_config(config_path):
    """設定ファイルを読み込む。存在しない場合はデフォルト設定を作成する。"""
    if not os.path.exists(config_path):
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
            print(f"デフォルト設定ファイルを生成しました: {config_path}")
            return DEFAULT_CONFIG
        except Exception as e:
            print(f"設定ファイルの生成に失敗しました: {e}", file=sys.stderr)
            return DEFAULT_CONFIG
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 欠落しているキーがあればデフォルト値で補完
        merged_config = DEFAULT_CONFIG.copy()
        merged_config.update(config)
        return merged_config
    except Exception as e:
        print(f"設定ファイルの読み込みに失敗しました: {e}。デフォルト設定を使用します。", file=sys.stderr)
        return DEFAULT_CONFIG


def main():
    parser = argparse.ArgumentParser(description="raspi4eyes: 4つのUVC入力を2x2でHDMIに出力するプログラム")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG_FILE, help="設定ファイルのパス")
    parser.add_argument("--fullscreen", action="store_true", help="フルスクリーン表示を強制する")
    parser.add_argument("--windowed", action="store_true", help="ウィンドウ表示を強制する")
    parser.add_argument("--debug-noise", action="store_true", help="各カメラのノイズ判定値をリアルタイム表示する（閾値調整用）")
    args = parser.parse_args()

    # 設定の読み込み
    config = load_config(args.config)
    
    # コマンドライン引数によるオーバーライド
    if args.fullscreen:
        config["fullscreen"] = True
    elif args.windowed:
        config["fullscreen"] = False

    devices = config["devices"]
    width = config["width"]
    height = config["height"]
    fps = config["fps"]
    use_mjpeg = config["use_mjpeg"]
    show_no_signal = config["show_no_signal_text"]
    detect_noise = config["detect_noise"]
    noise_threshold = config["noise_threshold"]

    print("--- 起動設定 ---")
    print(f"デバイス: {devices}")
    print(f"解像度: {width}x{height} @ {fps}fps")
    print(f"MJPEG圧縮: {use_mjpeg}")
    print(f"フルスクリーン: {config['fullscreen']}")
    print(f"砂嵐検出: {detect_noise} (閾値: {noise_threshold})")
    print("----------------")

    # カメラキャプチャスレッドの起動
    threads = []
    for dev in devices:
        t = CameraThread(dev, width, height, fps, use_mjpeg, detect_noise, noise_threshold)
        t.start()
        threads.append(t)

    # 表示ウィンドウの設定
    window_name = "raspi4eyes"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    
    if config["fullscreen"]:
        # フルスクリーンプロパティを設定
        cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    else:
        # ウィンドウの初期サイズ
        cv2.resizeWindow(window_name, width * 2, height * 2)

    # 黒い背景フレームの作成
    black_frame = np.zeros((height, width, 3), dtype=np.uint8)

    print("プログラムを開始しました。終了するには 'q' または ESC を押してください。")
    
    loop_count = 0
    try:
        while True:
            # 砂嵐デバッグ表示 (約1秒=30ループに1回)
            if args.debug_noise and loop_count % 30 == 0:
                noise_info = []
                for t in threads:
                    noise_info.append(f"{t.device}: {t.get_noise_value():.2f}")
                print(" | ".join(noise_info))
            loop_count += 1

            frames = []
            for i, t in enumerate(threads):
                frame = t.get_frame()
                if frame is None:
                    dummy = black_frame.copy()
                    if show_no_signal:
                        dev_name = devices[i]
                        text1 = "No Signal"
                        text2 = f"Device: {dev_name}"
                        
                        font = cv2.FONT_HERSHEY_SIMPLEX
                        font_scale = 0.6
                        thickness = 1
                        color = (150, 150, 150) # 薄いグレー
                        
                        # テキスト1の描画
                        size1 = cv2.getTextSize(text1, font, font_scale, thickness)[0]
                        x1 = (width - size1[0]) // 2
                        y1 = (height // 2) - 10
                        cv2.putText(dummy, text1, (x1, y1), font, font_scale, color, thickness, cv2.LINE_AA)
                        
                        # テキスト2の描画
                        size2 = cv2.getTextSize(text2, font, font_scale, thickness)[0]
                        x2 = (width - size2[0]) // 2
                        y2 = (height // 2) + 15
                        cv2.putText(dummy, text2, (x2, y2), font, font_scale, color, thickness, cv2.LINE_AA)
                    
                    frames.append(dummy)
                else:
                    # 念のためサイズを統一
                    if frame.shape[1] != width or frame.shape[0] != height:
                        frame = cv2.resize(frame, (width, height))
                    frames.append(frame)

            # 4画面足りない場合の補足（もしデバイス指定が4つ未満の場合などへの対応）
            while len(frames) < 4:
                frames.append(black_frame.copy())

            # 2x2に結合
            top_row = np.hstack((frames[0], frames[1]))
            bottom_row = np.hstack((frames[2], frames[3]))
            grid_frame = np.vstack((top_row, bottom_row))

            # 表示
            cv2.imshow(window_name, grid_frame)

            # キーイベント待機 (30fps程度に合わせるため約30ms待機)
            key = cv2.waitKey(33) & 0xFF
            if key == ord('q') or key == 27:  # 'q' または ESC
                break
    except KeyboardInterrupt:
        print("\nユーザーによる中断を受け付けました。")
    finally:
        print("スレッドを停止しています...")
        for t in threads:
            t.stop()
        for t in threads:
            t.join(timeout=1.0)
        cv2.destroyAllWindows()
        print("終了しました。")

if __name__ == "__main__":
    main()
