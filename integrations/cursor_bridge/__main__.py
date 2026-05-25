"""Run: python -m integrations.cursor_bridge"""

from __future__ import annotations

import logging
import sys

from integrations.cursor_bridge.server import serve_forever

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> int:
    httpd = serve_forever()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down cursor_bridge", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
