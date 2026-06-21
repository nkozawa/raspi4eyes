# Guide to Running in Headless Environments with Cage (English)

This document provides setup instructions for running `raspi4eyes` and outputting directly to HDMI using the `Cage` compositor on Linux systems without a desktop environment (such as Raspberry Pi OS Lite).

---

## 1. Install Required Packages

Install `Cage` (a lightweight Wayland kiosk compositor), `wlr-randr` (display configuration tool), and `seatd` (session manager daemon).

```bash
sudo apt update
sudo apt install -y cage xwayland wlr-randr seatd
```

---

## 2. Configure Permissions (User Groups)

To allow the user to control physical graphics devices and start a display session from an SSH terminal, assign the necessary group permissions.

1. Add your user to the `video`, `render`, and `seat` groups:
   ```bash
   sudo usermod -aG video,render,seat $USER
   ```
2. Enable and start the `seatd` service:
   ```bash
   sudo systemctl enable --now seatd
   ```
3. **【Important】You must log out and log back in (reconnect the SSH session) to apply the new group memberships.**
   ```bash
   exit
   ```

---

## 3. Create the Startup Script

Instead of forcing a specific OS resolution (which can cause issues with HDMI reconnection or timing), we now keep the OS resolution at the monitor's recommended setting (e.g., 1920x1080) and let the application scale the output.

### Content of `run.sh`
The script has already been created in your project directory with the following content:

```bash
#!/bin/bash

# Start the Python program using the virtual environment
cd "$(dirname "$0")"
.venv/bin/python raspi4eyes.py --fullscreen
```

*Ensure the script is executable:*
```bash
chmod +x run.sh
```

---

## 4. Run the Application (Manual Run)

Once setup is complete, execute the script with `cage`:

```bash
# Start Cage directly (seatd service handles session management in the background)
cage -- ./run.sh
```

To suppress non-fatal warning messages (such as red EGL warnings), prepend `WLR_LOG_LEVEL=error`:
```bash
WLR_LOG_LEVEL=error cage -- ./run.sh
```

---

## 5. Configure Automatic Startup on Boot (systemd service)

To automatically launch `Cage` and `raspi4eyes` on system startup, configure a `systemd` service.

### 1. Create the Service File
Create a new service configuration file at `/etc/systemd/system/raspi4eyes.service`:
```bash
sudo nano /etc/systemd/system/raspi4eyes.service
```

Add the following content (adjust `User` and `WorkingDirectory` if they differ on your system):

```ini
[Unit]
Description=raspi4eyes display service using Cage
After=network.target systemd-logind.service seatd.service
Wants=seatd.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/raspi4eyes
# Start a PAM session to automatically generate XDG_RUNTIME_DIR and other env vars
PAMName=login
ExecStart=/usr/bin/cage -s -- /home/pi/raspi4eyes/run.sh
Environment=WLR_LOG_LEVEL=error
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> [!IMPORTANT]
> Be sure to include `PAMName=login`. Without it, the `XDG_RUNTIME_DIR` environment variable required by Wayland won't be set, causing the service to fail with the error: `XDG_RUNTIME_DIR is not set in the environment`.

### 2. Enable and Start the Service
Register and launch the service:

```bash
# Reload systemd configuration
sudo systemctl daemon-reload

# Enable service at boot
sudo systemctl enable raspi4eyes.service

# Start the service immediately for testing
sudo systemctl start raspi4eyes.service
```

### 3. Check Status and Logs
Verify that the service is running correctly:

```bash
# Check service status
sudo systemctl status raspi4eyes.service

# View real-time logs
journalctl -u raspi4eyes.service -f
```

---

## 6. Troubleshooting

### Q1: `Could not open target tty: Permission denied`
* **Cause**: The group membership changes have not taken effect, or additional TTY permissions are needed.
* **Solution**: Ensure you have logged out and logged back in. If the issue persists, add your user to the `tty` group and log in again:
  ```bash
  sudo usermod -aG tty $USER
  ```

### Q2: `refusing to start / seatd exited prematurely`
* **Cause**: This happens if you try to use `seatd-launch` when the `seatd` service is already running in the background.
* **Solution**: Do not use `seatd-launch`. Run the application directly with `cage -- ./run.sh`.
