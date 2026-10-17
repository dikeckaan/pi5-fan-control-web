"""HTTP API and pages."""

import functools
import hmac
import json
import os
import secrets
import threading
import time
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

from . import __version__
from .config import ConfigError, validate_level, validate_mode
from .controller import BOOST_MAX_SECONDS

LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 60


class LoginThrottle:
    """Locks an address out for a while after repeated wrong passwords."""

    def __init__(self):
        self._lock = threading.Lock()
        self._failures = {}

    def locked_for(self, addr):
        with self._lock:
            count, until = self._failures.get(addr, (0, 0.0))
            return max(0, int(until - time.monotonic()))

    def failed(self, addr):
        with self._lock:
            count, _ = self._failures.get(addr, (0, 0.0))
            count += 1
            until = time.monotonic() + LOGIN_LOCKOUT_SECONDS if count >= LOGIN_MAX_FAILURES else 0.0
            self._failures[addr] = (0 if until else count, until)

    def succeeded(self, addr):
        with self._lock:
            self._failures.pop(addr, None)


def load_secret_key(data_dir):
    path = os.path.join(data_dir, ".secret_key")
    try:
        with open(path, "rb") as f:
            key = f.read()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_bytes(32)
    os.makedirs(data_dir, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def error(code, status=400, **params):
    return jsonify({"error": code, "params": params}), status


def create_app(controller, store, password=None, secret_key=None):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(__name__, template_folder=os.path.join(root, "templates"),
                static_folder=os.path.join(root, "static"))
    app.config.update(
        SECRET_KEY=secret_key or secrets.token_bytes(32),
        MAX_CONTENT_LENGTH=64 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=30 * 24 * 3600,
        JSON_SORT_KEYS=False,
    )
    throttle = LoginThrottle()
    auth_enabled = bool(password)

    def authed():
        return not auth_enabled or session.get("auth") is True

    @app.before_request
    def guard():
        if request.endpoint in ("static", "healthz", "login_page", "login"):
            return None
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            # Cross-site requests cannot send JSON without a CORS preflight we never
            # answer, and an Origin from another host is refused outright.
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                return error("forbidden_origin", 403)
            if not request.is_json:
                return error("unsupported_media", 415)
        if not authed():
            if request.path.startswith("/api/"):
                return error("unauthorized", 401)
            return redirect(url_for("login_page"))
        return None

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(ConfigError)
    def config_error(e):
        return jsonify(e.to_dict()), 400

    @app.errorhandler(413)
    def too_large(_):
        return error("too_large", 413)

    def json_body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ConfigError("invalid_json")
        return data

    def config_changed(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            resp = fn(*args, **kwargs)
            controller.tick_soon()
            return resp
        return wrapper

    # ------------------------------------------------------------ pages

    @app.get("/")
    def index():
        return render_template("index.html", auth_enabled=auth_enabled, version=__version__)

    @app.get("/login")
    def login_page():
        if authed():
            return redirect(url_for("index"))
        return render_template("login.html", version=__version__)

    @app.post("/api/login")
    def login():
        addr = request.remote_addr or "?"
        wait = throttle.locked_for(addr)
        if wait:
            return error("locked", 429, seconds=wait)
        data = request.get_json(silent=True) or {}
        given = str(data.get("password", "")).encode()
        if auth_enabled and hmac.compare_digest(given, password.encode()):
            throttle.succeeded(addr)
            session.clear()
            session["auth"] = True
            session.permanent = True
            return jsonify({"status": "ok"})
        throttle.failed(addr)
        return error("invalid_password", 401)

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify({"status": "ok"})

    @app.get("/healthz")
    def healthz():
        status = controller.status()
        ok = status["level"] is not None
        return jsonify({"ok": ok, "version": __version__}), 200 if ok else 503

    # ------------------------------------------------------------ state

    @app.get("/api/status")
    def status():
        return jsonify(controller.status())

    @app.get("/api/history")
    def history():
        try:
            since = float(request.args.get("since", 0))
        except ValueError:
            since = 0.0
        return jsonify({"points": controller.history_since(since)})

    # ------------------------------------------------------------ control

    @app.post("/api/mode")
    @config_changed
    def set_mode():
        mode = validate_mode(json_body().get("mode"))
        store.update(mode=mode)
        controller.event("mode_changed", mode=mode)
        return jsonify({"mode": mode})

    @app.post("/api/manual-level")
    @config_changed
    def set_manual_level():
        level = validate_level(json_body().get("level"), store.max_level)
        store.update(manual_level=level)
        controller.event("manual_level_changed", level=level)
        return jsonify({"manual_level": level})

    @app.post("/api/boost")
    def boost():
        try:
            seconds = int(json_body().get("seconds", 30))
            controller.boost(seconds)
        except (TypeError, ValueError):
            return error("invalid_seconds", min=1, max=BOOST_MAX_SECONDS)
        return jsonify({"boost_remaining": controller.boost_remaining()})

    @app.delete("/api/boost")
    def cancel_boost():
        return jsonify({"cancelled": controller.cancel_boost()})

    # ------------------------------------------------------------ config

    @app.get("/api/config")
    def get_config():
        return jsonify({"config": store.get(), "defaults": store.defaults(),
                        "max_level": store.max_level})

    @app.patch("/api/config")
    @config_changed
    def patch_config():
        data = json_body()
        allowed = {k: data[k] for k in ("mode", "manual_level", "auto_thresholds", "safety")
                   if k in data}
        cfg = store.update(**allowed)
        controller.event("config_updated")
        return jsonify({"config": cfg})

    @app.get("/api/config/export")
    def export_config():
        body = json.dumps(store.get(), indent=2) + "\n"
        return Response(body, mimetype="application/json", headers={
            "Content-Disposition": 'attachment; filename="pi5-fan-config.json"'})

    @app.post("/api/config/import")
    @config_changed
    def import_config():
        cfg = store.replace(json_body())
        controller.event("config_imported")
        return jsonify({"config": cfg})

    return app
