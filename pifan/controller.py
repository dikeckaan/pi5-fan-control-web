"""The fan control loop and every safety rule that protects the fan and the Pi."""

import collections
import logging
import threading
import time

from .hardware import HardwareError

log = logging.getLogger(__name__)

TICK_SECONDS = 1.0
HISTORY_EVERY = 2.0
HISTORY_SECONDS = 30 * 60

# Consecutive failed temperature reads before we assume the sensor is gone.
SENSOR_FAILURE_TICKS = 3
# Emergency cooling only ends once the SoC is this far below the emergency temperature.
EMERGENCY_RELEASE = 5
SPINUP_KICK_SECONDS = 2.0
# After a level change the fan needs a moment before its RPM means anything.
STALL_GRACE_SECONDS = 6.0
STALL_SECONDS = 10.0
STALL_MIN_RPM = 100
BOOST_MAX_SECONDS = 600
MAX_EVENTS = 50

SEVERITY = {
    "sensor_failure": "critical",
    "emergency": "critical",
    "fan_stall": "critical",
    "manual_override": "warning",
    "control_shared": "info",
}


def curve_lookup(curve, temp):
    level = 0
    for t, l in sorted((int(k), v) for k, v in curve.items()):
        if temp >= t:
            level = l
    return level


def curve_level(curve, temp, current, hysteresis):
    """Curve level with hysteresis: rise as soon as a threshold is crossed, but
    only fall once the temperature is `hysteresis` degrees below it."""
    up = curve_lookup(curve, temp)
    if up >= current:
        return up
    return min(current, curve_lookup(curve, temp + hysteresis))


class FanController:
    def __init__(self, hw, store, clock=time.monotonic, wall_clock=time.time):
        self.hw = hw
        self.store = store
        self.clock = clock
        self.wall_clock = wall_clock
        self.max_level = hw.max_state

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

        self.level = None
        self.target = None
        self.reason = "starting"
        self.temperature = None
        self.rpm = None
        self.exclusive = False

        # The level chosen by the rules, before the spin-up kick is applied.
        self._base = None
        self._last_step = 0.0
        self._last_write = 0.0
        self._sensor_failures = 0
        self._emergency = False
        self._boost_until = 0.0
        self._kick_until = 0.0
        self._stall_since = None
        self._last_history = None
        self._started_at = clock()

        self.alarms = {}
        self.events = collections.deque(maxlen=MAX_EVENTS)
        self.history = collections.deque(maxlen=int(HISTORY_SECONDS / HISTORY_EVERY))

    # ----------------------------------------------------------------- lifecycle

    def start(self):
        self.exclusive = self.hw.take_control()
        self._set_alarm("control_shared", not self.exclusive)
        self.event("started")
        self._thread = threading.Thread(target=self._run, name="fan-control", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.event("stopping")
        self.hw.release_control()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                # Never let a bug end the loop: the fan must keep being managed.
                log.exception("Fan control tick failed")
                try:
                    self.hw.write_level(self.max_level)
                except Exception:
                    pass
            self._stop.wait(TICK_SECONDS)

    # ----------------------------------------------------------------- events

    def event(self, key, **params):
        with self._lock:
            self.events.appendleft({"time": self.wall_clock(), "key": key, "params": params})
        log.info("event %s %s", key, params or "")

    def _set_alarm(self, key, active, **params):
        if active and key not in self.alarms:
            self.alarms[key] = {"key": key, "severity": SEVERITY[key],
                                "since": self.wall_clock(), "params": params}
            return True
        if not active and key in self.alarms:
            del self.alarms[key]
            return True
        return False

    # ----------------------------------------------------------------- commands

    def boost(self, seconds):
        seconds = int(seconds)
        if not 1 <= seconds <= BOOST_MAX_SECONDS:
            raise ValueError(seconds)
        with self._lock:
            self._boost_until = self.clock() + seconds
        self.event("boost_started", seconds=seconds)
        self.tick_soon()

    def cancel_boost(self):
        with self._lock:
            was_active = self._boost_until > self.clock()
            self._boost_until = 0.0
        if was_active:
            self.event("boost_cancelled")
            self.tick_soon()
        return was_active

    def boost_remaining(self, now=None):
        now = self.clock() if now is None else now
        return max(0.0, self._boost_until - now)

    def tick_soon(self):
        """Apply a command right away instead of waiting for the next loop iteration."""
        try:
            self.tick()
        except Exception:
            log.exception("Immediate tick failed")

    # ----------------------------------------------------------------- the loop

    def tick(self, now=None):
        with self._lock:
            now = self.clock() if now is None else now
            cfg = self.store.get()
            safety = cfg["safety"]

            self._read_temperature()
            self.rpm = self.hw.read_rpm()

            target, reason = self._desired(cfg, now)
            previous = self._base
            level = self._limit_rate(target, reason, safety, now)
            level = self._spinup(level, previous, safety, now)
            self._apply(level, now)
            self._check_stall(safety, now)
            self._record(now)

    def _read_temperature(self):
        try:
            self.temperature = self.hw.read_temp()
        except HardwareError as e:
            self._sensor_failures += 1
            if self._sensor_failures == 1:
                log.warning("%s", e)
            if self._sensor_failures >= SENSOR_FAILURE_TICKS:
                self.temperature = None
                if self._set_alarm("sensor_failure", True):
                    self.event("sensor_failure")
            return
        self._sensor_failures = 0
        if self._set_alarm("sensor_failure", False):
            self.event("sensor_recovered")

    def _desired(self, cfg, now):
        safety = cfg["safety"]
        temp = self.temperature
        current = self._base if self._base is not None else 0

        if "sensor_failure" in self.alarms or temp is None:
            # No reading yet, or the sensor is gone: cool as hard as possible.
            return self.max_level, "sensor_failsafe"

        if temp >= safety["emergency_temp"] and not self._emergency:
            self._emergency = True
            self._set_alarm("emergency", True, temp=round(temp, 1))
            self.event("emergency_on", temp=round(temp, 1))
        elif self._emergency and temp < safety["emergency_temp"] - EMERGENCY_RELEASE:
            self._emergency = False
            self._set_alarm("emergency", False)
            self.event("emergency_off", temp=round(temp, 1))
        if self._emergency:
            return self.max_level, "emergency"

        if self._boost_until:
            if now < self._boost_until:
                return self.max_level, "boost"
            self._boost_until = 0.0
            self.event("boost_finished")

        auto = curve_level(cfg["auto_thresholds"], temp, current, safety["hysteresis"])
        if cfg["mode"] == "manual":
            manual = cfg["manual_level"]
            override = temp >= safety["manual_override_temp"] and auto > manual
            if self._set_alarm("manual_override", override, temp=round(temp, 1)):
                self.event("manual_override_on" if override else "manual_override_off",
                           temp=round(temp, 1))
            return (auto, "manual_override") if override else (manual, "manual")

        if self._set_alarm("manual_override", False):
            self.event("manual_override_off", temp=round(temp, 1))
        return auto, "auto"

    def _limit_rate(self, target, reason, safety, now):
        """Speeding up is always immediate. In auto mode slowing down happens one
        level at a time with a minimum pause, so the fan does not hunt up and down
        around a threshold (the main source of motor and bearing wear)."""
        self.target, self.reason = target, reason
        base = self._base
        if base is None or target >= base or reason != "auto":
            level = target
        elif now - self._last_step < safety["min_change_interval"]:
            level = base
        else:
            level = base - 1
        if level != base:
            self._last_step = now
        self._base = level
        return level

    def _spinup(self, level, previous, safety, now):
        if level == 0:
            self._kick_until = 0.0
            return 0
        if now < self._kick_until:
            return self.max_level
        if previous == 0 and level < self.max_level and safety["spinup_kick"]:
            self._kick_until = now + SPINUP_KICK_SECONDS
            return self.max_level
        return level

    def _apply(self, level, now):
        actual = self.hw.read_level()
        if level != self.level:
            self._last_write = now
        elif actual is not None and actual != level:
            # Someone else (normally the kernel governor) changed the level.
            if self.exclusive:
                log.warning("Fan level changed externally (%s -> %s); restoring", level, actual)
        else:
            return
        try:
            self.hw.write_level(level)
            self.level = level
        except HardwareError as e:
            log.error("%s", e)

    def _check_stall(self, safety, now):
        spinning_expected = (self.level or 0) > 0 and now - self._last_write >= STALL_GRACE_SECONDS
        if not safety["stall_detection"] or self.rpm is None or not spinning_expected:
            self._stall_since = None
            if self._set_alarm("fan_stall", False):
                self.event("fan_stall_cleared")
            return
        if self.rpm >= STALL_MIN_RPM:
            self._stall_since = None
            if self._set_alarm("fan_stall", False):
                self.event("fan_stall_cleared")
            return
        if self._stall_since is None:
            self._stall_since = now
        elif now - self._stall_since >= STALL_SECONDS:
            if self._set_alarm("fan_stall", True, level=self.level):
                self.event("fan_stall", level=self.level)

    def _record(self, now):
        if self._last_history is not None and now - self._last_history < HISTORY_EVERY:
            return
        self._last_history = now
        temp = None if self.temperature is None else round(self.temperature, 1)
        self.history.append([round(self.wall_clock(), 1), temp, self.level, self.rpm])

    # ----------------------------------------------------------------- reporting

    def status(self):
        with self._lock:
            now = self.clock()
            cfg = self.store.get()
            return {
                "temperature": None if self.temperature is None else round(self.temperature, 1),
                "level": self.level,
                "target": self.target,
                "max_level": self.max_level,
                "rpm": self.rpm,
                "reason": "spinup" if now < self._kick_until else self.reason,
                "mode": cfg["mode"],
                "manual_level": cfg["manual_level"],
                "boost_remaining": round(self.boost_remaining(now), 1),
                "boost_max": BOOST_MAX_SECONDS,
                "exclusive_control": self.exclusive,
                "alarms": sorted(self.alarms.values(), key=lambda a: a["since"]),
                "events": list(self.events)[:20],
                "uptime": round(now - self._started_at),
            }

    def history_since(self, since=0.0):
        with self._lock:
            return [p for p in self.history if p[0] > since]
