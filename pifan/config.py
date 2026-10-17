"""Configuration: defaults, validation and crash-safe persistence."""

import copy
import errno
import json
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

DEFAULT_CURVE = {"0": 0, "50": 1, "60": 2, "65": 3, "70": 4}

DEFAULTS = {
    "mode": "auto",
    "manual_level": 2,
    "auto_thresholds": DEFAULT_CURVE,
    "safety": {
        # Above this the fan runs at full speed in every mode.
        "emergency_temp": 80,
        # In manual mode the fan never runs slower than the curve above this temperature.
        "manual_override_temp": 75,
        # The fan only slows down once the temperature is this far below a threshold.
        "hysteresis": 3,
        # Minimum seconds between two automatic step-downs.
        "min_change_interval": 10,
        "stall_detection": True,
        # Briefly run at full speed when starting from standstill so the motor spins up reliably.
        "spinup_kick": True,
    },
}

MODES = ("auto", "manual")
CURVE_MAX_POINTS = 16
CURVE_TEMP_RANGE = (0, 100)

# field: (min, max)
SAFETY_RANGES = {
    "emergency_temp": (60, 90),
    "manual_override_temp": (45, 90),
    "hysteresis": (0, 15),
    "min_change_interval": (0, 300),
}
SAFETY_FLAGS = ("stall_detection", "spinup_kick")


class ConfigError(ValueError):
    """A validation error with a machine-readable code so the UI can translate it."""

    def __init__(self, code, **params):
        self.code = code
        self.params = params
        super().__init__(f"{code} {params}" if params else code)

    def to_dict(self):
        return {"error": self.code, "params": self.params}


def _int(value, field):
    # bool is an int subclass; "true" is not a fan level.
    if isinstance(value, bool):
        raise ConfigError("invalid_field", field=field)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value)
    if not isinstance(value, int):
        raise ConfigError("invalid_field", field=field)
    return value


def validate_mode(mode):
    if mode not in MODES:
        raise ConfigError("invalid_mode")
    return mode


def validate_level(level, max_level):
    try:
        level = _int(level, "level")
    except ConfigError:
        raise ConfigError("invalid_level", max=max_level) from None
    if not 0 <= level <= max_level:
        raise ConfigError("invalid_level", max=max_level)
    return level


def validate_curve(curve, max_level):
    if not isinstance(curve, dict) or not curve:
        raise ConfigError("curve_empty")
    if len(curve) > CURVE_MAX_POINTS:
        raise ConfigError("curve_too_many", max=CURVE_MAX_POINTS)
    lo, hi = CURVE_TEMP_RANGE
    points = {}
    for raw_temp, raw_level in curve.items():
        try:
            temp = _int(raw_temp, "temp")
        except ConfigError:
            raise ConfigError("curve_bad_temp", min=lo, max=hi) from None
        if not lo <= temp <= hi:
            raise ConfigError("curve_bad_temp", min=lo, max=hi)
        if temp in points:
            raise ConfigError("curve_duplicate", temp=temp)
        try:
            points[temp] = validate_level(raw_level, max_level)
        except ConfigError:
            raise ConfigError("curve_bad_level", max=max_level) from None
    levels = [points[t] for t in sorted(points)]
    # A hotter SoC must never get less cooling.
    if any(b < a for a, b in zip(levels, levels[1:])):
        raise ConfigError("curve_not_monotonic")
    return {str(t): points[t] for t in sorted(points)}


def validate_safety(safety):
    if not isinstance(safety, dict):
        raise ConfigError("invalid_field", field="safety")
    out = {}
    for field, (lo, hi) in SAFETY_RANGES.items():
        value = _int(safety.get(field), field)
        if not lo <= value <= hi:
            raise ConfigError("out_of_range", field=field, min=lo, max=hi)
        out[field] = value
    for field in SAFETY_FLAGS:
        if not isinstance(safety.get(field), bool):
            raise ConfigError("invalid_field", field=field)
        out[field] = safety[field]
    if out["manual_override_temp"] > out["emergency_temp"]:
        raise ConfigError("override_above_emergency")
    return out


def merge_defaults(cfg):
    """Fill in missing keys so configs from older versions keep working."""
    merged = copy.deepcopy(DEFAULTS)
    if not isinstance(cfg, dict):
        return merged
    for key in ("mode", "manual_level", "auto_thresholds"):
        if key in cfg:
            merged[key] = cfg[key]
    if isinstance(cfg.get("safety"), dict):
        merged["safety"].update(cfg["safety"])
    return merged


def validate(cfg, max_level):
    if not isinstance(cfg, dict):
        raise ConfigError("invalid_json")
    cfg = merge_defaults(cfg)
    return {
        "mode": validate_mode(cfg["mode"]),
        "manual_level": validate_level(cfg["manual_level"], max_level),
        "auto_thresholds": validate_curve(cfg["auto_thresholds"], max_level),
        "safety": validate_safety(cfg["safety"]),
    }


def default_config(max_level):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["manual_level"] = min(cfg["manual_level"], max_level)
    cfg["auto_thresholds"] = {t: min(l, max_level) for t, l in DEFAULT_CURVE.items()}
    return validate(cfg, max_level)


def atomic_write_json(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(tmp, path)
    except OSError as e:
        # A single file bind-mounted into a container cannot be replaced; overwrite in place.
        if e.errno not in (errno.EBUSY, errno.EXDEV, errno.EPERM):
            raise
        os.remove(tmp)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")


class ConfigStore:
    """Thread-safe in-memory config backed by a JSON file.

    The file is only read at startup; every change goes through validate() and
    is written atomically, so a crash or power cut can never leave a half-written
    config behind.
    """

    def __init__(self, path, max_level):
        self.path = path
        self.max_level = max_level
        self._lock = threading.Lock()
        self._cfg = self._load()

    def _load(self):
        if not os.path.exists(self.path):
            cfg = default_config(self.max_level)
            self._save(cfg)
            return cfg
        try:
            with open(self.path) as f:
                raw = json.load(f)
            cfg = validate(raw, self.max_level)
        except (OSError, ValueError) as e:
            backup = f"{self.path}.invalid-{int(time.time())}"
            log.error("Config %s is invalid (%s); backing it up to %s and using defaults",
                      self.path, e, backup)
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            cfg = default_config(self.max_level)
        self._save(cfg)
        return cfg

    def _save(self, cfg):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        atomic_write_json(self.path, cfg)

    def get(self):
        with self._lock:
            return copy.deepcopy(self._cfg)

    def replace(self, cfg):
        cfg = validate(cfg, self.max_level)
        with self._lock:
            self._save(cfg)
            self._cfg = cfg
        return copy.deepcopy(cfg)

    def update(self, **changes):
        with self._lock:
            cfg = copy.deepcopy(self._cfg)
            if "safety" in changes:
                safety = changes.pop("safety")
                if not isinstance(safety, dict):
                    raise ConfigError("invalid_field", field="safety")
                cfg["safety"].update(safety)
            cfg.update(changes)
            cfg = validate(cfg, self.max_level)
            self._save(cfg)
            self._cfg = cfg
        return copy.deepcopy(cfg)

    def defaults(self):
        return default_config(self.max_level)
