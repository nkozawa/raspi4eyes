# Guide to Setting Up Overlay File System for Turnkey Systems (English)

This document explains how to enable the **Overlay File System (OverlayFS)** on Raspberry Pi OS. Enabling OverlayFS allows the Raspberry Pi to boot instantly, operate without write operations to the physical storage, and prevent SD card corruption when powered off without a safe shutdown.

---

## 1. Benefits of Overlay File System

On embedded systems like Raspberry Pi, abrupt power loss or pulling the plug without executing a graceful shutdown (`sudo poweroff`) can corrupt the file system on the SD card, preventing the OS from booting next time.

**Advantages of OverlayFS (Read-Only Mode):**
* **Robustness**: All disk write operations are redirected to temporary memory (RAM). The physical content on the SD card remains untouched. You can shut off power at any time without risking corruption.
* **Extended SD Card Lifespan**: Minimizing write operations drastically reduces SD card wear and tear.
* **State Reset**: Rebooting the system discards all runtime changes (temporary logs, process cache), reverting the system to a clean, default state.

---

## 2. Prerequisites

* The `raspi4eyes` program and its `systemd` auto-start service must be configured and running successfully.
* It is highly recommended to back up your SD card image before proceeding.

---

## 3. Configuration Steps (Using raspi-config)

Raspberry Pi OS provides a built-in menu utility to easily enable and manage OverlayFS. This is the safest and most convenient method.

1. Launch the configuration utility:
   ```bash
   sudo raspi-config
   ```

2. Navigate through the menu options:
   * Select **`4 Performance Options`**.
     *(Note: On older OS versions, this might be under `Advanced Options`.)*
   * Select **`P3 Overlay File System`**.

3. When asked: "Would you like the overlay file system to be enabled?", select **`<Yes>`**.

4. When asked: "Would you like the boot partition to be write-protected?", select **`<Yes>`**.
   *(Note: Unless you plan to change boot configurations frequently, write-protecting the boot partition is recommended for security and safety.)*

5. Once the success message displays, press `<Ok>`, select `<Finish>` on the main menu, and exit.

6. Reboot the system to apply changes:
   ```bash
   sudo reboot
   ```

---

## 4. Verification

After rebooting, log in via SSH to confirm that OverlayFS is active.

1. Check disk utilization:
   ```bash
   df -h
   ```
   Verify that the filesystem for the `/` (root directory) partition is listed as **`overlay`**.
   
   **Example Output:**
   ```text
   Filesystem      Size  Used Avail Use% Mounted on
   overlay          3.7G  120M  3.6G   4% /
   ```

2. Perform a write test to verify that changes are discarded upon reboot:
   ```bash
   # Create a test file
   echo "test" > ~/test_file.txt
   
   # Reboot the system
   sudo reboot
   ```
   After rebooting, check if `~/test_file.txt` exists. If it is gone, the read-only overlay is working correctly.

---

## 5. Maintenance and System Updates (How to Disable OverlayFS)

To update programs (e.g. `git pull`), install packages, or modify configurations permanantly, you must temporarily disable OverlayFS.

1. Open the configuration utility:
   ```bash
   sudo raspi-config
   ```

2. Select the same options:
   * **`4 Performance Options`** ➔ **`P3 Overlay File System`**
   * Select **`<No>`** when asked to enable the overlay file system.
   * Select **`<No>`** when asked to write-protect the boot partition (this disables write-protection).

3. Exit the configuration utility and reboot:
   ```bash
   sudo reboot
   ```

4. After rebooting, the system is in standard Read-Write mode. Perform your updates (such as updating `raspi4eyes` files).

5. **Once updates are complete, follow "3. Configuration Steps" again to re-enable OverlayFS and return to the safe read-only state.**
