#!/usr/bin/env python3
"""
Development server with auto-reload (similar to React hot reload).

Uses Flask's built-in server and Werkzeug's reloader:
  - Python changes (e.g. app.py) restart the process automatically.
  - Template/HTML changes apply on the next browser refresh (debug mode).

For production-style serving, use: python run.py
"""

import os
import threading
import webbrowser

from app import app

HOST = "0.0.0.0"
PORT = 5000
URL = f"http://127.0.0.1:{PORT}"


def _open_browser_once() -> None:
    print(f"Opening browser at {URL}")
    webbrowser.open_new(URL)


if __name__ == "__main__":
    # Werkzeug runs a parent watchdog process; only open the browser in the worker.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Timer(1.0, _open_browser_once).start()

    print("WalkingPad dev server — auto-reload enabled (Ctrl+C to stop)")
    print(f"Local:   {URL}")
    print(f"Network: http://{HOST}:{PORT}")

    app.run(
        host=HOST,
        port=PORT,
        debug=True,
        use_reloader=True,
        use_debugger=True,
    )
