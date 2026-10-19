import asyncio
import logging
import os
import threading
import webbrowser
from threading import Timer
import time
from collections import deque

from bleak import BleakScanner
from flask import Flask, render_template, redirect, url_for, jsonify, make_response, request
from ph4_walkingpad.pad import Controller, WalkingPad

# ── Logging Setup ────────────────────────────────────────────────────────
# All print() statements will be replaced with this logging configuration.
# It provides timed, leveled output. Set level=logging.DEBUG to see verbose messages.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

# ── Conversion constants ─────────────────────────────────────────────────
KM_TO_MI = 0.621371
KMH_TO_MPH = 0.621371
KCAL_PER_MILE = 95  # rough kcal per mile

# Speed control constants
MAX_SPEED_KMH = 6.0  # Approx 3.7 mph, a common max for these pads
MIN_SPEED_KMH = 1.0
SPEED_STEP = 0.3  # Speed change per button press in km/h
SLOW_WALK_SPEED_KMH = 4.5 # Approx 2.8 MPH

SAVED_TREADMILL_COOKIE = "walkingpad_device_name"
SAVED_TREADMILL_COOKIE_MAX_AGE = 365 * 24 * 60 * 60


def kcal_estimate(miles: float) -> float:
    return KCAL_PER_MILE * miles

# In app.py
def format_seconds_to_hms(total_seconds):
    """Converts total seconds to H:MM:SS string format."""
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours}:{minutes:02}:{seconds:02}"


# ── Flask & global state ────────────────────────────────────────────────
app = Flask(__name__)

connected = connecting = connection_failed = False
ble_loop: asyncio.AbstractEventLoop | None = None
controller: Controller | None = None
_pad_address: str | None = None
_pad_name: str | None = None
_auto_pause_grace_until = 0
speed_history = deque(maxlen=15)

session_active = belt_running = False
resume_speed_kmh = 2.0  # default if none yet

current_speed_kmh = current_distance_km = 0.0
current_steps = 0
current_calories = 0.0
current_session_active_seconds = 0 

_last_dev_dist = _last_dev_steps = 0
_connect_failed_clear_saved = False


# ── Context processor so templates always know flags ────────────────────
@app.context_processor
def inject_flags():
    return dict(
        connected=connected,
        connecting=connecting,
        connection_failed=connection_failed,
        pad_address=_pad_address,
        pad_name=_pad_name,
    )


def _set_saved_treadmill_cookie(response, name: str | None):
    if name:
        response.set_cookie(
            SAVED_TREADMILL_COOKIE,
            name,
            max_age=SAVED_TREADMILL_COOKIE_MAX_AGE,
            samesite="Lax",
            httponly=False,
        )
    return response


# ── BLE helpers ─────────────────────────────────────────────────────────
async def _connect_to_pad() -> bool:
    global controller, _pad_address, _pad_name

    if not _pad_address and not _pad_name:
        logging.error("No treadmill selected. Pick a device from the list in the web UI.")
        return False

    dev = None
    if _pad_address:
        logging.info("Attempting to connect to selected address: %s", _pad_address)
        try:
            dev = await BleakScanner.find_device_by_address(_pad_address, timeout=10)
        except Exception as exc:
            logging.warning("Failed to find device by address: %s", exc)
            dev = None

    if not dev and _pad_name:
        logging.info("Attempting to connect to saved treadmill name: %s", _pad_name)
        try:
            dev = await BleakScanner.find_device_by_name(_pad_name, timeout=10)
        except Exception as exc:
            logging.warning("Failed to find device by name: %s", exc)
            dev = None

    if not dev:
        logging.error(
            "Could not find treadmill (address=%s, name=%s). Ensure it is on, in range, and scan again.",
            _pad_address,
            _pad_name,
        )
        return False

    _pad_address = dev.address
    if dev.name:
        _pad_name = dev.name
    logging.info("Device found: name=%r address=%s", _pad_name, _pad_address)

    controller = Controller()
    await controller.run(dev.address)

    if hasattr(controller, "client") and controller.client:
        _register_disconnect_callback(controller.client)

    await controller.switch_mode(WalkingPad.MODE_MANUAL)

    def _status_cb(_sender, st):
        try:
            if isinstance(st, dict):
                dist = st.get("dist", 0)
                steps = st.get("steps", 0)
                speed = st.get("speed", 0)
            else:
                dist = getattr(st, "dist", 0)
                steps = getattr(st, "steps", 0)
                speed = getattr(st, "speed", 0)
            process_status_packet(dist, steps, speed)
            logging.debug(f"Push d={dist} s={steps} v={speed}")
        except Exception as exc:
            logging.warning(f"status_cb error: {exc}")

    controller.on_cur_status_received = _status_cb

    if hasattr(controller, "enable_notifications"):
        try:
            await controller.enable_notifications()
        except Exception as exc:
            logging.warning(f"enable_notifications failed: {exc}")
    return True


def process_status_packet(dev_dist, dev_steps, dev_speed):
    """Update cumulative stats from raw values AND handle auto-pause."""
    global belt_running, resume_speed_kmh, _auto_pause_grace_until
    global current_speed_kmh, current_distance_km, current_steps, current_calories
    global _last_dev_dist, _last_dev_steps

    new_reported_speed_kmh = dev_speed / 10.0

    # Continuously populate the speed history with stable, non-zero speeds.
    if belt_running and new_reported_speed_kmh > MIN_SPEED_KMH:
        speed_history.append(new_reported_speed_kmh)

    # AUTO-PAUSE LOGIC
    if time.time() > _auto_pause_grace_until:
        if belt_running and new_reported_speed_kmh == 0 and current_speed_kmh > 0:
            logging.info("Belt has stopped unexpectedly. Auto-pausing session.")
            
            # Use the OLDEST speed from history to ignore the deceleration phase.
            if speed_history:
                resume_speed_kmh = speed_history[0] # Use the first (oldest) item
            else:
                # Fallback if pause happens too quickly after starting
                resume_speed_kmh = MIN_SPEED_KMH

            belt_running = False

    # CUMULATIVE STATS LOGIC (is unchanged)
    # ...
    if dev_dist < _last_dev_dist:
        _last_dev_dist = 0
    current_distance_km += (dev_dist - _last_dev_dist) / 100.0
    _last_dev_dist = dev_dist

    if dev_steps < _last_dev_steps:
        _last_dev_steps = 0
    current_steps += dev_steps - _last_dev_steps
    _last_dev_steps = dev_steps

    current_speed_kmh = new_reported_speed_kmh
    current_calories = kcal_estimate(current_distance_km * KM_TO_MI)


async def _stats_monitor():
    """Active monitor: explicitly request a status packet every second."""
    global current_session_active_seconds
    logging.info("Stats monitor started")
    while belt_running:
        
        if belt_running: # Double check, as belt_running can change between await calls
            current_session_active_seconds += 1
        
        try:
            status = await controller.ask_stats()
            if status:
                if isinstance(status, dict):
                    dist = status.get("dist", 0)
                    steps = status.get("steps", 0)
                    speed = status.get("speed", 0)
                else:
                    dist = getattr(status, "dist", 0)
                    steps = getattr(status, "steps", 0)
                    speed = getattr(status, "speed", 0)
                process_status_packet(dist, steps, speed)
                logging.debug(f"Poll {status}")
        except Exception as exc:
            logging.warning(f"ask_stats error: {exc}")
        await asyncio.sleep(1)


def _ble_thread():
    global connected, connecting, connection_failed, ble_loop, _connect_failed_clear_saved
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ble_loop = loop

    if not loop.run_until_complete(_connect_to_pad()):
        connecting = False
        connection_failed = True
        _connect_failed_clear_saved = True
        return

    connected = True
    connecting = False
    try:
        loop.run_forever()
    finally:
        connected = False
        loop.close()


def _start_ble_thread():
    global connecting, connection_failed
    if connected or connecting:
        return
    if not _pad_address and not _pad_name:
        logging.warning("Cannot start BLE thread without a device address or saved name.")
        return
    connecting = True
    connection_failed = False
    threading.Thread(target=_ble_thread, daemon=True).start()

def _handle_disconnect(client):
    """Callback function to handle unexpected disconnections."""
    global connected, belt_running, connecting, connection_failed
    if connected: # Only log if we thought we were connected
        logging.warning("Device has disconnected unexpectedly.")
    connected = False
    belt_running = False
    connecting = False
    connection_failed = True


def _register_disconnect_callback(bleak_client) -> None:
    """Register disconnect handler (bleak 3.x moved this off BleakClient)."""
    if bleak_client is None:
        return

    if hasattr(bleak_client, "set_disconnected_callback"):
        bleak_client.set_disconnected_callback(_handle_disconnect)
        return

    backend = getattr(bleak_client, "_backend", None)
    if backend is not None and hasattr(backend, "set_disconnected_callback"):
        backend.set_disconnected_callback(lambda: _handle_disconnect(bleak_client))
        return

    logging.warning("Unable to register BLE disconnect callback for this bleak version.")


_ble_scan_lock = threading.Lock()


async def _discover_nearby_devices(timeout: float = 10.0) -> list[dict]:
    """Scan for BLE devices and return name, address, and RSSI when available."""
    logging.info("Scanning for nearby Bluetooth devices (%.0fs)...", timeout)
    devices_out: list[dict] = []

    try:
        discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except TypeError:
        discovered = await BleakScanner.discover(timeout=timeout)

    if isinstance(discovered, dict):
        for address, entry in discovered.items():
            if isinstance(entry, tuple) and len(entry) == 2:
                device, adv_data = entry
                name = device.name or getattr(adv_data, "local_name", None)
                rssi = getattr(adv_data, "rssi", None)
            else:
                device = entry
                name = getattr(device, "name", None)
                rssi = None
            devices_out.append(
                {"name": name, "address": address, "rssi": rssi}
            )
    else:
        for device in discovered:
            devices_out.append(
                {
                    "name": device.name,
                    "address": device.address,
                    "rssi": None,
                }
            )

    devices_out.sort(
        key=lambda d: (-(d["rssi"] if d["rssi"] is not None else -999), d["address"])
    )

    for d in devices_out:
        logging.info(
            "BLE nearby: name=%r address=%s rssi=%s",
            d["name"],
            d["address"],
            d["rssi"],
        )
    logging.info("BLE scan finished: %d device(s) found", len(devices_out))
    return devices_out


def _run_ble_device_scan(timeout: float = 10.0) -> list[dict]:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_discover_nearby_devices(timeout))
    finally:
        loop.close()


# ── Flask routes ────────────────────────────────────────────────────────
@app.route("/")
def root():
    global connection_failed, _pad_name, _connect_failed_clear_saved

    if not connected:
        show_saved_connect_error = _connect_failed_clear_saved
        saved_name = (request.cookies.get(SAVED_TREADMILL_COOKIE) or "").strip()

        if saved_name and not connecting and not connection_failed:
            _pad_name = saved_name
            _start_ble_thread()

        resp = make_response(
            render_template(
                "connecting.html",
                show_saved_connect_error=show_saved_connect_error,
            )
        )
        if _connect_failed_clear_saved:
            resp.delete_cookie(SAVED_TREADMILL_COOKIE)
            _connect_failed_clear_saved = False
            connection_failed = False
            _pad_name = None
        return resp

    time_active_display = "0:00:00" # Default for start/paused if not running
    if session_active: # Only calculate if a session is or was active
        time_active_display = format_seconds_to_hms(current_session_active_seconds)

    if not session_active:
        # For start_session, always show 0 time initially
        return render_template("start_session.html", time_active="0:00:00")

    template = "active_session.html" if belt_running else "paused_session.html"

    return render_template(
        template,
        speed_mph=current_speed_kmh * KMH_TO_MPH,
        speed_kmh=current_speed_kmh,
        distance_mi=current_distance_km * KM_TO_MI,
        distance_km=current_distance_km,
        steps=current_steps,
        calories=current_calories,
        time_active=time_active_display,
    )


@app.route("/reconnect", endpoint="reconnect")
@app.route("/manual_reconnect", endpoint="manual_reconnect")
def reconnect():
    if not connected and not connecting and (_pad_address or _pad_name):
        _start_ble_thread()
    return redirect(url_for("root"))


@app.route("/connect", methods=["POST"], endpoint="connect_device")
def connect_device():
    global _pad_address, _pad_name, connection_failed

    if connected or connecting:
        return redirect(url_for("root"))

    address = (request.form.get("address") or "").strip()
    name = (request.form.get("name") or "").strip()
    if not address and not name:
        connection_failed = True
        return redirect(url_for("root"))

    _pad_address = address or None
    _pad_name = name or None
    connection_failed = False
    _start_ble_thread()
    resp = redirect(url_for("root"))
    if name:
        _set_saved_treadmill_cookie(resp, name)
    return resp


@app.route("/api/ble_scan")
def ble_scan():
    """Discover nearby BLE devices for the device picker UI."""
    if connected or connecting:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Cannot scan while connecting or already connected to a device.",
                    "devices": [],
                }
            ),
            409,
        )

    if not _ble_scan_lock.acquire(blocking=False):
        return jsonify({"ok": False, "scanning": True, "devices": []})

    try:
        timeout = min(30.0, max(3.0, float(request.args.get("timeout", 10))))
        devices = _run_ble_device_scan(timeout)
        return jsonify({"ok": True, "scanning": False, "devices": devices})
    except Exception as exc:
        logging.exception("BLE scan failed")
        return jsonify({"ok": False, "error": str(exc), "devices": []}), 500
    finally:
        _ble_scan_lock.release()


@app.route("/start")
def start_session():
    """Begin a new session: reset counters, start belt, launch stats monitor."""
    global session_active, belt_running, current_distance_km, current_steps, current_calories, resume_speed_kmh
    global current_session_active_seconds

    if not connected:
        return redirect(url_for("root"))

    current_distance_km = current_steps = current_calories = 0.0
    current_session_active_seconds = 0
    resume_speed_kmh = 2.0
    speed_history.clear() 

    session_active = True
    belt_running = True

    async def seq():
        try:
            await controller.start_belt()
            await asyncio.sleep(0.5)
            asyncio.create_task(_stats_monitor())
        except Exception as exc:
            logging.error(f"Start sequence error: {exc}")

    asyncio.run_coroutine_threadsafe(seq(), ble_loop)
    return redirect(url_for("root"))


# ── Pause / Resume ───────────────────────────────────────────────────────

@app.route("/pause", endpoint="pause")
@app.route("/pause_session", endpoint="pause_session")
def pause_session():
    global belt_running, resume_speed_kmh
    if not belt_running:
        return redirect(url_for("root"))
    
    # Use the most recent speed from our history for manual pause
    if speed_history:
        resume_speed_kmh = speed_history[-1]

    belt_running = False
    asyncio.run_coroutine_threadsafe(controller.stop_belt(), ble_loop)
    return redirect(url_for("root"))


@app.route("/resume", endpoint="resume")
@app.route("/resume_session", endpoint="resume_session")
def resume_session():
    global belt_running, _auto_pause_grace_until, session_active # session_active ensures we only resume active sessions
    
    if not session_active: # Can't resume if no session was active
        logging.warning("Resume called but no active session.")
        return redirect(url_for("root"))

    if belt_running: # Already running, do nothing
        logging.info("Resume called but belt is already running.")
        return redirect(url_for("root"))

    # --- CRITICAL FIX: Optimistically set state for UI and grace period ---
    logging.info("Resume button clicked. Setting app state to active.")
    belt_running = True
    _auto_pause_grace_until = time.time() + 7 # Generous 7-second grace period for commands to take effect

    async def seq():
        try:
            logging.info("Attempting resume: Sending wake-up and start sequence to device...")
            
            # Standard wake-up and start sequence
            await controller.switch_mode(WalkingPad.MODE_STANDBY)
            await asyncio.sleep(0.5) 
            await controller.switch_mode(WalkingPad.MODE_MANUAL)
            await asyncio.sleep(0.5) 
            
            await controller.start_belt()
            await asyncio.sleep(0.5) 
            
            logging.info(f"Setting speed to {resume_speed_kmh:.1f} km/h.")
            await controller.change_speed(int(resume_speed_kmh * 10))
            await asyncio.sleep(0.5) # Allow speed change to propagate
            
            # Start the monitor if it wasn't running or to be sure
            asyncio.create_task(_stats_monitor())
            logging.info("Resume sequence commands sent, monitor ensured.")

        except Exception as exc:
            logging.error(f"Error during resume sequence, device may have disconnected: {exc}")
            _handle_disconnect(None) # This will set belt_running = False and connected = False
                                     # The frontend polling will then reload to the correct disconnected/connecting page.

    asyncio.run_coroutine_threadsafe(seq(), ble_loop)
    # The redirect will now happen after belt_running is True in the main thread.
    return redirect(url_for("root"))


# ── Speed Controls ───────────────────────────────────────────────────────
def _apply_speed_change(new_speed_kmh: float) -> None:
    if not connected or controller is None or ble_loop is None:
        logging.warning("Speed change ignored: not connected to treadmill.")
        return
    dev_speed = int(round(new_speed_kmh * 10))
    asyncio.run_coroutine_threadsafe(controller.change_speed(dev_speed), ble_loop)


@app.route("/decrease_speed")
def decrease_speed():
    """Decrease the belt speed by one step."""
    if not belt_running:
        return redirect(url_for("root"))

    new_speed_kmh = max(MIN_SPEED_KMH, current_speed_kmh - SPEED_STEP)
    _apply_speed_change(new_speed_kmh)
    return redirect(url_for("root"))

@app.route("/decrease_speed_double")
def decrease_speed_double():
    """Decrease the belt speed by one step."""
    if not belt_running:
        return redirect(url_for("root"))

    new_speed_kmh = max(MIN_SPEED_KMH, current_speed_kmh - (SPEED_STEP * 2))
    _apply_speed_change(new_speed_kmh)
    return redirect(url_for("root"))

@app.route("/slow_speed")
def slow_speed():
    """Set the belt speed to a predefined slow walk speed."""
    if not belt_running:
        return redirect(url_for("root"))
    
    _apply_speed_change(SLOW_WALK_SPEED_KMH)
    return redirect(url_for("root"))

@app.route("/increase_speed")
def increase_speed():
    """Increase the belt speed by one step."""
    if not belt_running:
        return redirect(url_for("root"))

    new_speed_kmh = min(MAX_SPEED_KMH, current_speed_kmh + SPEED_STEP)
    _apply_speed_change(new_speed_kmh)
    return redirect(url_for("root"))

@app.route("/increase_speed_double")
def increase_speed_double():
    """Increase the belt speed by one step."""
    if not belt_running:
        return redirect(url_for("root"))

    new_speed_kmh = min(MAX_SPEED_KMH, current_speed_kmh + (SPEED_STEP * 2))
    _apply_speed_change(new_speed_kmh)
    return redirect(url_for("root"))


@app.route("/max_speed")
def max_speed():
    """Set the belt speed to maximum."""
    if not belt_running:
        return redirect(url_for("root"))
    
    _apply_speed_change(MAX_SPEED_KMH)
    return redirect(url_for("root"))


# ── Live JSON endpoint ───────────────────────────────────────────────────
@app.route("/stats", endpoint="get_stats")
def stats_json():
    # Calculate formatted_time_active within the function scope
    formatted_time_active = format_seconds_to_hms(current_session_active_seconds)

    speed_kmh = round(current_speed_kmh, 1)
    speed_mph = round(current_speed_kmh * KMH_TO_MPH, 1)
    data = dict(
        is_connected=connected,      
        is_running=belt_running,     
        speed=speed_mph,
        speed_mph=speed_mph,
        speed_kmh=speed_kmh,
        distance=round(current_distance_km * KM_TO_MI, 2),
        distance_mi=round(current_distance_km * KM_TO_MI, 2),
        distance_km=round(current_distance_km, 2),
        steps=current_steps,
        calories=round(current_calories),
        time_active=formatted_time_active 
    )

    resp = make_response(jsonify(data))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── Shutdown endpoint ──────────────────────────────────────────────────
@app.route("/shutdown", methods=['POST'])
def shutdown():
    """Forcefully shut down the Flask application process."""
    logging.info("Server shutting down via forceful exit...")
    os._exit(0)


# BLE connection starts when the user picks a device from the web UI (/connect).