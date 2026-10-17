"""Entry point: wires hardware, config, controller and web server together."""

import logging
import os
import shutil
import signal
import sys

from .config import ConfigStore
from .controller import FanController
from .hardware import SimulatedFan, SysfsFan
from .web import create_app, load_secret_key

log = logging.getLogger("pifan")


def env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def main():
    logging.basicConfig(
        level=os.environ.get("FAN_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    data_dir = os.environ.get("FAN_DATA_DIR", "data")
    config_path = os.environ.get("FAN_CONFIG", os.path.join(data_dir, "config.json"))
    # Carry over the config.json that v1 kept next to app.py.
    if not os.path.exists(config_path) and os.path.exists("config.json") \
            and os.path.abspath("config.json") != os.path.abspath(config_path):
        os.makedirs(os.path.dirname(config_path) or ".", exist_ok=True)
        shutil.copy("config.json", config_path)
        log.info("Migrated config.json to %s", config_path)

    hw = SimulatedFan() if env_flag("FAN_SIMULATE") else SysfsFan()
    store = ConfigStore(config_path, hw.max_state)
    controller = FanController(hw, store)
    password = os.environ.get("FAN_PASSWORD") or None
    secret = os.environ.get("FAN_SECRET_KEY", "").encode() or load_secret_key(data_dir)
    app = create_app(controller, store, password=password, secret_key=secret)

    def shutdown(signum, _frame):
        log.info("Received signal %d, shutting down", signum)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    controller.start()
    try:
        from waitress import serve
        host = os.environ.get("FAN_HOST", "0.0.0.0")
        port = int(os.environ.get("FAN_PORT", "5000"))
        log.info("Serving on http://%s:%d (auth %s)", host, port, "on" if password else "off")
        serve(app, host=host, port=port, threads=4, ident="pi5-fan")
    finally:
        # Always hand the fan back to the kernel at full speed, whatever ended the server.
        controller.stop()


if __name__ == "__main__":
    sys.exit(main())
