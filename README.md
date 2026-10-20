# WalkingPad Web Controller

A simple, web-based application to control compatible KingSmith WalkingPad and similar BLE treadmills (for example Dynamax models that use the same protocol), offering features and a user experience that aims to improve upon the official app and remote.

This project was born out of a desire for a more flexible walking-pad experience, specifically to address limitations of official controls such as the inability to truly pause and resume a session (especially after stepping off) and the lack of cumulative session stat tracking across these pauses.

## Key Features

* **Web-Based Interface:** Control your treadmill from any device on your local network using a web browser.
* **Bluetooth LE Connection:** Scan nearby devices, pick your treadmill from a list, and connect by address and advertised name.
* **Remembered Treadmill:** After you connect once, the app saves the device name (browser cookie and `localStorage`) and tries to reconnect automatically on the next visit. If that fails, the saved name is cleared and the device list is shown again.
* **Full Session Control:**
    * Start new walking sessions.
    * Pause sessions via the app.
    * Resume sessions at the last used speed.
* **Intelligent Auto-Pause & Resume:**
    * Automatically detects if you've stepped off the pad or if it's stopped by the remote, and transitions the app to a paused state.
    * Resumes from these auto-pauses at a stable speed from before the interruption.
* **Dynamic Stat Tracking:** Displays and updates live session data:
    * Current speed (mph and km/h)
    * Cumulative distance (miles and km)
    * Cumulative steps
    * Estimated calories burned
    * Cumulative active time (H:M:S format)
* **Precise Speed Adjustments:**
    * Single-step increase/decrease buttons.
    * Double-step increase/decrease buttons (two steps per press).
    * "Max Speed" preset (defaults to ~3.7 mph / 6.0 km/h, configurable).
    * "Slow Walk" preset (defaults to ~2.8 mph / 4.5 km/h, configurable).
* **User-Friendly Conveniences:**
    * Opens your default web browser on startup when using `run.py` or `dev.py`.
    * `start_app.bat` (Windows) and `dev.sh` / `dev.bat` for easier launching.
    * Clean, modern UI with connection status in the header.
    * "Close" button in the app to shut down the server.
* **Cross-Platform Server:** Runs on Windows, macOS, or Linux with Python and a Bluetooth adapter.

## Screenshots

Below are representative screenshots of the application in action:

### Start Session Screen
Shown after connecting, before a session has begun.

![Start Session Screen](screenshots/start.png)

### Active Session Screen
Live stats and speed controls while walking.

![Active Session Screen](screenshots/active.png)

### Paused Session Screen
Cumulative stats and resume after a manual or automatic pause.

![Paused Session Screen](screenshots/paused.png)

## Supported Models

This application is known to work with the **WalkingPad C1** model.

It should also work with other treadmills supported by the **`ph4-walkingpad`** library (for example A1 and R1 PRO), and with **rebranded pads** that expose the same BLE services (for example some **Dynamax** units). Compatibility depends on the model and firmware—use the Bluetooth scan screen to confirm your device appears and connects.

## Credit & Core Dependency

This application relies on the **`ph4-walkingpad`** Python library by **ph4x** for Bluetooth communication and protocol support.

* **ph4-walkingpad on PyPI:** [https://pypi.org/project/ph4-walkingpad/](https://pypi.org/project/ph4-walkingpad/)

## Prerequisites

* Python 3.8 or newer (3.13 tested).
* A Bluetooth adapter on the computer running this application.
* A compatible treadmill.
* `pip` for installing dependencies.

## Setup and Installation

1. **Clone the repository** (or download and extract the project), then open a terminal in the project folder.

2. **Create a virtual environment:**
    ```bash
    python -m venv venv
    ```

3. **Activate the virtual environment:**
    * Windows: `venv\Scripts\activate`
    * macOS/Linux: `source venv/bin/activate`

4. **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

    `requirements.txt` includes: `flask`, `bleak`, `ph4-walkingpad`, and `waitress`.

## Running the Application

### Production-style server (recommended for normal use)

Uses **Waitress** for a stable WSGI server:

1. Power on your treadmill and ensure Bluetooth is enabled on your computer.
2. Activate the virtual environment.
3. Run:
    ```bash
    python run.py
    ```
4. The browser opens at `http://127.0.0.1:5000`.
5. Stop the server with **Ctrl+C** in the terminal, or use **Close** in the web UI.

**Windows:** `start_app.bat` activates the venv and runs `run.py`.

### Development server (auto-reload)

For local development, use Flask’s dev server with automatic reload when Python files change:

```bash
./dev.sh
```

On Windows: `dev.bat`, or `python dev.py` with the venv activated.

Templates refresh on the next page load in debug mode. After a code reload, you may need to scan and connect to Bluetooth again.

## How to Use

### 1. Connection

* On first launch (or after a failed saved connection), the app **scans for nearby Bluetooth devices** (~5 seconds) and shows a list with name, address, and signal strength.
* Tap **Connect** on your treadmill (for example `Dynamax` or `WalkingPad`).
* The next time you open the app, it **attempts to reconnect using the saved device name**. If that fails, the saved name is removed and you’ll see the scan list again.
* The header shows **Connecting**, **Connected**, or **Disconnected**.

### 2. Starting a Session

* When connected and no session is active, use **Start New Session**.
* Click **Start** to begin; stats start at zero.

### 3. Active Session

* Stats update live (time, speed, distance, steps, calories).
* Controls:
    * **Slow** — preset slow walk speed.
    * **--** / **-** — decrease speed by two steps / one step.
    * **Pause** — stop the belt and pause the session.
    * **+** / **++** — increase speed by one step / two steps.
    * **Max** — maximum configured speed.
* Stepping off the pad or stopping via the remote triggers **auto-pause** and the paused screen.

### 4. Paused Session

* View cumulative stats and press **Resume** to continue at the previous speed.

### 5. Ending the Application

* Use **Close** in the header to shut down the server process.

## Customization

Edit speed and scan behaviour in `app.py`:

| Constant | Purpose |
|----------|---------|
| `MAX_SPEED_KMH` | Max speed for the **Max** button |
| `MIN_SPEED_KMH` | Minimum belt speed |
| `SPEED_STEP` | Single-step change for +/- buttons (km/h) |
| `SLOW_WALK_SPEED_KMH` | **Slow** button target speed |
| `SAVED_TREADMILL_COOKIE` | Cookie name for remembered treadmill (default `walkingpad_device_name`) |

Double-step buttons use `SPEED_STEP * 2`.

## Troubleshooting

* **Connection / scan issues**
    * Turn on the computer’s Bluetooth and power on the treadmill.
    * Disconnect the treadmill from other apps (official phone app, etc.).
    * Move closer to the device and run **Scan nearby devices** again.
    * Pick the correct entry from the list—names vary by brand (WalkingPad, Dynamax, etc.).
    * On macOS, BLE addresses are UUIDs; reconnecting by **saved name** is intentional.
    * Check the terminal running `run.py` or `dev.py` for Python logs.

* **`waitress-serve` not found**
    * Activate the project venv and run `pip install -r requirements.txt`, or use `python run.py` only from that environment.

* **Icons not displaying**
    * Bootstrap Icons load from a CDN; the machine running the browser needs internet access. See `templates/base.html` for the CDN link.

* **Unexpected behaviour**
    * Review server logs and the browser developer console (F12) for errors.
