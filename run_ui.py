#!/usr/bin/env python3
"""Launch the RadioGrid web UI.

Usage::

    python run_ui.py              # default port 5000
    python run_ui.py --port 8080  # custom port
"""

from __future__ import annotations

import argparse
import threading
import webbrowser

from radiogrid.ui.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the RadioGrid web UI.")
    parser.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    args = parser.parse_args()

    app = create_app()
    url = f"http://{args.host}:{args.port}"

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f"RadioGrid UI → {url}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
