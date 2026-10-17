"""Access to the Raspberry Pi 5 thermal sensor and fan through sysfs.

A simulator with the same interface is included so the panel can be developed
and demoed on machines without a Pi.
"""

import glob
import logging
import os
import random
import threading
import time

log = logging.getLogger(__name__)

THERMAL_ROOT = "/sys/class/thermal"
HWMON_GLOBS = (
    "/sys/devices/platform/cooling_fan/hwmon/hwmon*/fan1_input",
    "/sys/class/hwmon/hwmon*/fan1_input",
)
USERSPACE_POLICY = "user_space"

# Anything outside this range is a sensor glitch, not a real SoC temperature.
MIN_SANE_TEMP = -40.0
MAX_SANE_TEMP = 150.0


class HardwareError(Exception):
    pass


def _read(path):
    with open(path) as f:
        return f.read().strip()


def _write(path, value):
    with open(path, "w") as f:
        f.write(str(value))


class SysfsFan:
    """The real hardware: thermal_zone0 for temperature, the pwm-fan cooling device for speed."""

    def __init__(self, root=THERMAL_ROOT, zone="thermal_zone0", hwmon_globs=HWMON_GLOBS):
        self.zone_path = os.path.join(root, zone)
        self.temp_path = os.path.join(self.zone_path, "temp")
        self.policy_path = os.path.join(self.zone_path, "policy")
        self.cdev_path = self._find_cooling_device(root)
        self.state_path = os.path.join(self.cdev_path, "cur_state")
        try:
            self.max_state = int(_read(os.path.join(self.cdev_path, "max_state")))
        except (OSError, ValueError):
            self.max_state = 4
        self.rpm_path = next((p for g in hwmon_globs for p in sorted(glob.glob(g))), None)
        self._original_policy = None
        log.info("Using cooling device %s (max_state=%d), rpm=%s",
                 self.cdev_path, self.max_state, self.rpm_path or "unavailable")

    @staticmethod
    def _find_cooling_device(root):
        candidates = sorted(glob.glob(os.path.join(root, "cooling_device*")))
        for path in candidates:
            try:
                if _read(os.path.join(path, "type")) == "pwm-fan":
                    return path
            except OSError:
                continue
        if candidates:
            return candidates[0]
        raise HardwareError(f"No cooling device found under {root}")

    def read_temp(self):
        try:
            temp = int(_read(self.temp_path)) / 1000
        except (OSError, ValueError) as e:
            raise HardwareError(f"Cannot read temperature: {e}") from e
        if not MIN_SANE_TEMP <= temp <= MAX_SANE_TEMP:
            raise HardwareError(f"Implausible temperature reading: {temp}")
        return temp

    def read_level(self):
        try:
            return int(_read(self.state_path))
        except (OSError, ValueError):
            return None

    def write_level(self, level):
        level = max(0, min(self.max_state, int(level)))
        try:
            _write(self.state_path, level)
        except OSError as e:
            raise HardwareError(f"Cannot set fan level: {e}") from e

    def read_rpm(self):
        if not self.rpm_path:
            return None
        try:
            return int(_read(self.rpm_path))
        except (OSError, ValueError):
            return None

    def take_control(self):
        """Switch the thermal zone to the user_space governor so the kernel stops
        overwriting our fan level. Returns True when we have exclusive control."""
        try:
            current = _read(self.policy_path)
            available = _read(os.path.join(self.zone_path, "available_policies")).split()
        except OSError:
            return False
        if current == USERSPACE_POLICY:
            # Left over from a run that was killed before it could clean up;
            # step_wise is the Pi kernel's default governor.
            if "step_wise" in available:
                self._original_policy = "step_wise"
            return True
        if USERSPACE_POLICY not in available:
            log.warning("Kernel has no %s thermal governor; will keep re-applying the fan level",
                        USERSPACE_POLICY)
            return False
        try:
            _write(self.policy_path, USERSPACE_POLICY)
        except OSError as e:
            log.warning("Cannot switch thermal policy: %s", e)
            return False
        self._original_policy = current
        log.info("Thermal policy %s -> %s", current, USERSPACE_POLICY)
        return True

    def release_control(self):
        """Hand the fan back to the kernel. The fan is left at full speed first so
        that, whatever happens next, the Pi is never left uncooled; the kernel
        governor steps it down on its own."""
        try:
            self.write_level(self.max_state)
        except HardwareError as e:
            log.error("Could not set fan to full speed on release: %s", e)
        if self._original_policy:
            try:
                _write(self.policy_path, self._original_policy)
                log.info("Thermal policy restored to %s", self._original_policy)
            except OSError as e:
                log.error("Could not restore thermal policy: %s", e)
            self._original_policy = None


class SimulatedFan:
    """A toy thermal model: the SoC heats up under a wandering load and the fan cools it."""

    max_state = 4

    def __init__(self, seed=None):
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._temp = 48.0
        self._level = 0
        self._load = 0.5
        self._last = time.monotonic()

    def _advance(self):
        now = time.monotonic()
        dt = min(now - self._last, 5.0)
        self._last = now
        self._load = min(1.0, max(0.0, self._load + self._rng.uniform(-0.08, 0.08) * dt))
        equilibrium = 42 + 38 * self._load - 5.5 * self._level
        self._temp += (equilibrium - self._temp) * min(1.0, 0.05 * dt)

    def read_temp(self):
        with self._lock:
            self._advance()
            return round(self._temp + self._rng.uniform(-0.15, 0.15), 1)

    def read_level(self):
        return self._level

    def write_level(self, level):
        with self._lock:
            self._advance()
            self._level = max(0, min(self.max_state, int(level)))

    def read_rpm(self):
        if self._level == 0:
            return 0
        return int(1500 + 950 * self._level + self._rng.uniform(-60, 60))

    def take_control(self):
        return True

    def release_control(self):
        self._level = self.max_state
