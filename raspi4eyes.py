#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
# OpenCVの内部警告ログ（VIDIOC_QBUF等のファイル記述子エラーなど）を抑制 / Suppress OpenCV internal warning logs (e.g., file descriptor errors like VIDIOC_QBUF)
os.environ["OPENCV_LOG_LEVEL"] = "ERROR"

import sys
import json
import time
import threading
import argparse
import platform
import numpy as np
import cv2

# OS判定 / OS determination
IS_WINDOWS = (os.name == "nt")
IS_LINUX = sys.platform.startswith("linux")

# OS依存のキャプチャバックエンド / OS-dependent capture backend
# WindowsはDirectShow、LinuxはV4L2を明示的に指定する。 / Explicitly specify DirectShow for Windows and V4L2 for Linux.
# (MJPEG圧縮の要求や解像度設定を確実に効かせ、想定外のバックエンド選択を避けるため) / (To ensure MJPEG compression requests and resolution settings take effect and avoid unexpected backend selection)
if IS_WINDOWS:
    CAPTURE_BACKEND = cv2.CAP_DSHOW
    BACKEND_NAME = "DirectShow"
elif IS_LINUX:
    CAPTURE_BACKEND = cv2.CAP_V4L2
    BACKEND_NAME = "V4L2"
else:
    CAPTURE_BACKEND = cv2.CAP_ANY
    BACKEND_NAME = "Auto"

# Windowsでデフォルト選択するカメラのデバイス名 / Camera device name to default to on Windows
DEFAULT_WINDOWS_DEVICE_NAME = "USB2.0 PC CAMERA"

# OS依存のデフォルトデバイス / OS-dependent default devices
# Windowsはカメラのインデックス番号（名前解決失敗時のフォールバック）、Linuxは /dev/video パス。 / Windows uses camera index numbers (fallback when name resolution fails), Linux uses /dev/video paths.
if IS_WINDOWS:
    DEFAULT_DEVICES = [0, 1, 2, 3]
else:
    DEFAULT_DEVICES = ["/dev/video0", "/dev/video2", "/dev/video4", "/dev/video6"]

# デフォルト設定 / Default settings
DEFAULT_CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "devices": DEFAULT_DEVICES,
    # Windowsのみ有効。空でなければ、この名前(部分一致)のカメラを上位から最大4台自動選択し / Valid only on Windows. If not empty, automatically select up to 4 cameras matching this name (partial match)
    # devices より優先する。Linuxでは無視される。 / with priority over "devices". Ignored on Linux.
    "windows_device_name": DEFAULT_WINDOWS_DEVICE_NAME if IS_WINDOWS else "",
    "width": 640,
    "height": 480,
    "fps": 30,
    "fullscreen": True,
    "use_mjpeg": True,
    "show_no_signal_text": True,
    "detect_noise": True,
    "noise_threshold": 0.4
}


def list_windows_cameras():
    """DirectShowのビデオ入力デバイスを列挙し [(index, name), ...] を返す。 / Enumerate DirectShow video input devices and return [(index, name), ...].
    pygrabber が未導入の場合は None を返す（0台検出の [] とは区別する）。 / Returns None if pygrabber is not installed (distinguished from [] for 0 devices detected)."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
        return list(enumerate(names))
    except ImportError:
        return None
    except Exception as e:
        print(f"Failed to enumerate cameras: {e}", file=sys.stderr)
        return []


class DeviceSource:
    """カメラの「開くべきデバイスID」を供給する抽象。 / Abstraction that supplies the device ID to be opened.
    CameraThread はこのインターフェース越しにデバイスを取得/解放するだけでよく、 / CameraThread only needs to acquire/release devices through this interface,
    OSや取得方式（固定 index/path か、名前による動的解決か）を意識しない。 / and does not need to care about the OS or acquisition method (fixed index/path vs. dynamic name resolution)."""

    def acquire(self, owner):
        """開くべき dev_id (int または path) を返す。利用不可なら None。 / Return the dev_id (int or path) to open. Return None if unavailable."""
        raise NotImplementedError

    def release(self, owner):
        """owner が確保していたデバイスを解放する。 / Release the device that was claimed by owner."""
        pass


class FixedDeviceSource(DeviceSource):
    """固定の index/path を返すソース（Linux の /dev/video や明示インデックス用）。 / Source that returns a fixed index/path (for Linux /dev/video or explicit index).
    スレッドごとに1つ持つ。常に同じ dev_id を返すだけで、解放は何もしない。 / Each thread has one instance. It simply returns the same dev_id and does nothing on release."""

    def __init__(self, device):
        try:
            self.dev_id = int(device)
        except (ValueError, TypeError):
            self.dev_id = device

    def acquire(self, owner):
        return self.dev_id


class NameResolvedSource(DeviceSource):
    """デバイス名一致のカメラインデックスを実行時に動的割り当てするソース（Windows用）。 / Source that dynamically allocates camera indices at runtime based on matching device name (for Windows).
    全スレッドで1インスタンスを共有し、二重取得を防止する。起動後の切断/再接続や、 / Shares a single instance across all threads to prevent double-claiming. It tracks
    後から接続されたカメラにも追従する。 / disconnections/reconnections and cameras connected later."""

    def __init__(self, name, max_count=4):
        self.name_l = name.lower()
        self.max_count = max_count
        self.lock = threading.Lock()
        self.claimed = {}  # dev_index -> owner

    def acquire(self, owner):
        with self.lock:
            cams = list_windows_cameras()
            if not cams:  # None(pygrabber未導入) または [](0台) / None (pygrabber not installed) or [] (0 devices)
                return None
            matched = [idx for idx, nm in cams
                       if self.name_l in (nm or "").lower()][:self.max_count]
            for idx in matched:
                if idx not in self.claimed:
                    self.claimed[idx] = owner
                    return idx
            return None

    def release(self, owner):
        with self.lock:
            for idx in [k for k, v in self.claimed.items() if v == owner]:
                del self.claimed[idx]


class CameraThread(threading.Thread):
    def __init__(self, label, width, height, fps, use_mjpeg, detect_noise, noise_threshold, source):
        super().__init__()
        self.device = label   # 表示・ログ用ラベル / Label for display and logging
        self.width = width
        self.height = height
        self.fps = fps
        self.use_mjpeg = use_mjpeg
        self.detect_noise = detect_noise
        self.noise_threshold = noise_threshold
        self.source = source  # DeviceSource: 開くデバイスの取得/解放を担う / DeviceSource: responsible for acquiring/releasing the device to open
        self.frame = None
        self.running = True
        self.daemon = True
        self.current_noise_val = 0.0
        self.is_noise_active = False
        self.lock = threading.Lock()
        
    def run(self):
        cap = None
        last_retry = 0
        retry_interval = 3.0  # 秒 / seconds
        consecutive_failures = 0
        max_consecutive_failures = 15  # 連続失敗許容回数（約0.5秒分） / Allowable consecutive failures (approx. 0.5s worth of frames)
        noise_counter = 0
        normal_counter = 0

        while self.running:
            if cap is None or not cap.isOpened():
                now = time.time()
                # 接続試行の間隔を確実に制限（切断されてから必ず3秒待つ） / Ensure connection attempts are rate-limited (always wait 3s after disconnection)
                if now - last_retry > retry_interval:
                    last_retry = now

                    # 開くべきデバイスをソースから取得（固定/名前解決の差はソースが吸収） / Acquire the device to open from source (source abstracts fixed vs. name resolution difference)
                    dev_id = self.source.acquire(self)
                    if dev_id is None:
                        # 利用可能なデバイスが今は無い（名前一致の空きが無い等）→ 待機して再試行 / No available device at the moment (e.g., no matching name is free) -> wait and retry
                        with self.lock:
                            self.frame = None
                        cap = None
                        time.sleep(0.5)
                        continue

                    print(f"[{self.device}] Attempting to connect... (dev={dev_id})")
                    cap = cv2.VideoCapture(dev_id, CAPTURE_BACKEND)
                    if cap.isOpened():
                        # MJPEG設定 (Raspberry Piでの複数カメラ帯域不足対策に重要) / MJPEG setting (important for addressing camera bandwidth issues on Raspberry Pi)
                        if self.use_mjpeg:
                            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))

                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                        cap.set(cv2.CAP_PROP_FPS, self.fps)
                        print(f"[{self.device}] Connection successful (dev={dev_id})")
                        consecutive_failures = 0
                        noise_counter = 0
                        normal_counter = 0
                        self.is_noise_active = False
                    else:
                        cap = None
                        self.source.release(self)  # 確保したデバイスを解放（固定ソースは何もしない） / Release the acquired device (fixed source does nothing)
                        print(f"[{self.device}] Connection failed (dev={dev_id})")
                
                if cap is None:
                    time.sleep(0.5)
                    continue

            ret, frame = cap.read()
            if not ret:
                consecutive_failures += 1
                # 一時的なエラー（信号待ちなど）を許容し、連続失敗時のみ再接続へ / Allow temporary errors (e.g., waiting for signal) and only reconnect on consecutive failures
                if consecutive_failures >= max_consecutive_failures:
                    print(f"[{self.device}] Failed to read frames consecutively ({consecutive_failures} times). Reconnecting...")
                    cap.release()
                    cap = None
                    self.source.release(self)  # 次回はソースから取り直す（名前ソースは再解決） / Re-acquire from source next time (re-resolves name sources)
                    with self.lock:
                        self.frame = None
                        self.current_noise_val = 0.0
                        self.is_noise_active = False
                    last_retry = time.time()  # 次回接続まで3秒のクールダウンを設定 / Set a 3-second cooldown before the next connection attempt
                else:
                    time.sleep(0.03)  # 一時的失敗時は少し待ってループを回す / Sleep briefly and retry loop on temporary failure
                continue
            
            # 読み込み成功時は失敗カウンターをリセット / Reset failure counter on successful read
            consecutive_failures = 0
            
            # 砂嵐（ノイズ）判定 / Noise (static) detection
            if self.detect_noise:
                is_noise = self.check_noise(frame)
                if is_noise:
                    noise_counter += 1
                    normal_counter = 0
                else:
                    normal_counter += 1
                    noise_counter = 0
                
                # チャタリング防止: 15フレーム連続でノイズならノイズ状態、5フレーム連続で正常なら正常状態 / Debounce: Noise state if 15 consecutive frames are noise; Normal state if 5 consecutive frames are normal
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
            
            # CPU負荷低減のための僅かなスリープ / Brief sleep to reduce CPU usage
            time.sleep(0.001)

        if cap is not None:
            cap.release()
        self.source.release(self)

    def check_noise(self, frame):
        """画像が砂嵐（ランダムノイズ）または無信号黒画面かどうかを判定する / Determine if the frame is static (random noise) or a no-signal black screen"""
        try:
            # 高速化のために小さく縮小して処理 / Resize to a smaller resolution for faster processing
            small = cv2.resize(frame, (80, 60))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            
            # 1. ぼかし前の標準偏差（生コントラスト）を計算 / 1. Calculate standard deviation before blurring (raw contrast)
            _, stddev_raw = cv2.meanStdDev(gray)
            val_raw = stddev_raw[0][0]
            
            # 生のコントラストが極めて低い場合は、単一色（黒画面やブルーバック）とみなす / If raw contrast is extremely low, treat as solid color (black or blue screen)
            if val_raw < 3.0:
                self.current_noise_val = 0.0
                return True
            
            # 2. 大きめのカーネルでガウシアンブラーをかけて細かいノイズ（砂嵐や走査線）を完全に平滑化 / 2. Apply Gaussian blur with a large kernel to completely smooth out fine noise (static or scanlines)
            blurred = cv2.GaussianBlur(gray, (15, 15), 0)
            
            # 3. ぼかし後の標準偏差（全体的な明暗のコントラスト）を計算 / 3. Calculate standard deviation after blurring (overall brightness/darkness contrast)
            _, stddev_blur = cv2.meanStdDev(blurred)
            val_blur = stddev_blur[0][0]
            
            # 4. コントラスト比（ぼかし後 / ぼかし前）を算出 / 4. Calculate contrast ratio (blurred / raw)
            # 砂嵐：ぼかすと明暗差がほぼ消滅するため、比率は 0 に近くなる (例: 0.03) / Static: Blurring almost eliminates brightness differences, making the ratio close to 0 (e.g., 0.03)
            # 正常：ぼかしても大まかな構図（空と地面等）が残るため、比率は大きくなる (例: 0.60) / Normal: Rough composition (e.g. sky and ground) remains even after blurring, so the ratio is larger (e.g., 0.60)
            ratio = val_blur / val_raw
            self.current_noise_val = ratio
            
            # コントラスト比が閾値以下（平坦化した割合が非常に大きい ＝ 砂嵐）ならノイズと判定 / If the contrast ratio is below the threshold (very high flatting ratio = static), determine as noise
            return self.current_noise_val < self.noise_threshold
        except Exception:
            return False

    def get_frame(self):
        with self.lock:
            if self.frame is None:
                return None
            return self.frame.copy()

    def get_noise_value(self):
        """現在のノイズ判定値を取得する / Get the current noise evaluation value"""
        return self.current_noise_val

    def stop(self):
        self.running = False


def load_config(config_path):
    """設定ファイルを読み込む。存在しない場合はデフォルト設定を作成する。 / Load the configuration file. If it does not exist, create default settings."""
    if not os.path.exists(config_path):
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
            print(f"Generated default config file: {config_path}")
            return DEFAULT_CONFIG
        except Exception as e:
            print(f"Failed to generate config file: {e}", file=sys.stderr)
            return DEFAULT_CONFIG
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 欠落しているキーがあればデフォルト値で補完 / Complement missing keys with default values
        merged_config = DEFAULT_CONFIG.copy()
        merged_config.update(config)
        return merged_config
    except Exception as e:
        print(f"Failed to load config file: {e}. Using default settings.", file=sys.stderr)
        return DEFAULT_CONFIG


def main():
    parser = argparse.ArgumentParser(description="raspi4eyes: 4つのUVC入力を2x2でHDMIに出力するプログラム / Program to display 4 UVC inputs in a 2x2 grid on HDMI output")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG_FILE, help="設定ファイルのパス / Path to configuration file")
    parser.add_argument("--fullscreen", action="store_true", help="フルスクリーン表示を強制する / Force fullscreen display")
    parser.add_argument("--windowed", action="store_true", help="ウィンドウ表示を強制する / Force windowed display")
    parser.add_argument("--debug-noise", action="store_true", help="各カメラのノイズ判定値をリアルタイム表示する（閾値調整用） / Display real-time noise evaluation values for each camera (for adjusting thresholds)")
    args = parser.parse_args()

    # 設定の読み込み / Load configuration
    config = load_config(args.config)
    
    # コマンドライン引数によるオーバーライド / Override settings with command line arguments
    if args.fullscreen:
        config["fullscreen"] = True
    elif args.windowed:
        config["fullscreen"] = False

    # デバイスソースとラベルを決定する。 / Determine device sources and labels.
    # - Windows + windows_device_name: 名前で動的解決するソースを全スレッドで共有。 / - Windows + windows_device_name: Share name-based dynamic resolution source among all threads.
    #   起動後の切断/再接続や後付けカメラに追従し、インデックスへはフォールバックしない。 /   It tracks disconnection/reconnection and cameras connected later, and does not fallback to indices.
    # - それ以外: config の devices を固定の index/path ソースとして使う。 / - Otherwise: Use "devices" from config as fixed index/path sources.
    max_slots = 4
    device_name = config.get("windows_device_name", "")
    if IS_WINDOWS and device_name:
        cams = list_windows_cameras()
        if cams is None:
            print("pygrabber is required for device name lookup. Please run 'pip install pygrabber'.",
                  file=sys.stderr)
            sys.exit(1)
        print("--- Detected Cameras ---")
        for idx, cam_name in cams:
            print(f"  [{idx}] {cam_name}")
        shared_source = NameResolvedSource(device_name, max_slots)
        # スロットごとに同じ共有ソースを渡す。実インデックスは実行時に名前で解決される。 / Pass the same shared source for each slot. The actual index is resolved by name at runtime.
        labels = [f"{device_name} #{i}" for i in range(max_slots)]
        device_specs = [(label, shared_source) for label in labels]
    else:
        device_specs = [(str(dev), FixedDeviceSource(dev)) for dev in config["devices"]]

    devices = [label for label, _ in device_specs]
    width = config["width"]
    height = config["height"]
    fps = config["fps"]
    use_mjpeg = config["use_mjpeg"]
    show_no_signal = config["show_no_signal_text"]
    detect_noise = config["detect_noise"]
    noise_threshold = config["noise_threshold"]

    print("--- Startup Settings ---")
    print(f"OS: {platform.system()} / Backend: {BACKEND_NAME}")
    print(f"Devices: {devices}")
    print(f"Resolution: {width}x{height} @ {fps}fps")
    print(f"MJPEG Compression: {use_mjpeg}")
    print(f"Fullscreen: {config['fullscreen']}")
    print(f"Static Noise Detection: {detect_noise} (Threshold: {noise_threshold})")
    print("----------------")

    # カメラキャプチャスレッドの起動
    threads = []
    for label, source in device_specs:
        t = CameraThread(label, width, height, fps, use_mjpeg, detect_noise, noise_threshold,
                         source=source)
        t.start()
        threads.append(t)

    # 表示ウィンドウの設定 / Configure display window
    window_name = "raspi4eyes"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    
    if config["fullscreen"]:
        # フルスクリーンプロパティを設定 / Set fullscreen property
        cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    else:
        # ウィンドウの初期サイズ / Initial window size
        cv2.resizeWindow(window_name, width * 2, height * 2)

    # 黒い背景フレームの作成 / Create a black background frame
    black_frame = np.zeros((height, width, 3), dtype=np.uint8)

    print("Program started. Press 'q' or ESC to exit.")
    
    loop_count = 0
    try:
        while True:
            # 砂嵐デバッグ表示 (約1秒=30ループに1回) / Display static debug info (once every 30 loops, approx. 1s)
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
                        color = (150, 150, 150) # 薄いグレー / Light gray
                        
                        # テキスト1の描画 / Draw text1
                        size1 = cv2.getTextSize(text1, font, font_scale, thickness)[0]
                        x1 = (width - size1[0]) // 2
                        y1 = (height // 2) - 10
                        cv2.putText(dummy, text1, (x1, y1), font, font_scale, color, thickness, cv2.LINE_AA)
                        
                        # テキスト2の描画 / Draw text2
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

            # 4画面足りない場合の補足（もしデバイス指定が4つ未満の場合などへの対応） / Fill in missing frames if there are fewer than 4 (handling cases with fewer than 4 specified devices, etc.)
            while len(frames) < 4:
                frames.append(black_frame.copy())

            # 2x2に結合 / Combine into 2x2
            top_row = np.hstack((frames[0], frames[1]))
            bottom_row = np.hstack((frames[2], frames[3]))
            grid_frame = np.vstack((top_row, bottom_row))

            # 表示 / Display
            cv2.imshow(window_name, grid_frame)

            # キーイベント待機 (30fps程度に合わせるため約30ms待機) / Wait for key events (approx. 30ms sleep to match ~30fps)
            key = cv2.waitKey(33) & 0xFF
            if key == ord('q') or key == 27:  # 'q' または ESC
                break
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        print("Stopping threads...")
        for t in threads:
            t.stop()
        for t in threads:
            t.join(timeout=1.0)
        cv2.destroyAllWindows()
        print("Finished.")

if __name__ == "__main__":
    main()
