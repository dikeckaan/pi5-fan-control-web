from conftest import no_kick

from pifan.controller import (BOOST_MAX_SECONDS, SENSOR_FAILURE_TICKS, STALL_GRACE_SECONDS,
                              STALL_SECONDS, curve_level)

CURVE = {"0": 0, "50": 1, "60": 2, "65": 3, "70": 4}


def test_curve_hysteresis():
    assert curve_level(CURVE, 61, 0, 3) == 2
    # 58 is below the 60 threshold but within the 3° hysteresis band.
    assert curve_level(CURVE, 58, 2, 3) == 2
    assert curve_level(CURVE, 56, 2, 3) == 1


def test_auto_follows_curve(controller, fan, store):
    no_kick(store)
    fan.temp = 62
    controller.tick()
    assert fan.level == 2 and controller.reason == "auto"


def test_step_down_is_gradual(controller, fan, store, clock):
    no_kick(store)
    fan.temp = 72
    controller.tick()
    assert fan.level == 4
    fan.temp = 30
    clock.t += 1
    controller.tick()
    assert fan.level == 4  # too soon after the last change
    levels = []
    for _ in range(4):
        clock.t += 10
        controller.tick()
        levels.append(fan.level)
    assert levels == [3, 2, 1, 0]


def test_step_up_is_immediate(controller, fan, store, clock):
    no_kick(store)
    fan.temp = 52
    controller.tick()
    fan.temp = 75
    clock.t += 0.5
    controller.tick()
    assert fan.level == 4


def test_emergency_overrides_manual(controller, fan, store, clock):
    store.update(mode="manual", manual_level=0)
    fan.temp = 81
    controller.tick()
    assert fan.level == 4 and controller.reason == "emergency"
    assert "emergency" in controller.alarms
    fan.temp = 77  # still within the release band
    clock.t += 1
    controller.tick()
    assert controller.reason == "emergency"
    fan.temp = 74
    clock.t += 1
    controller.tick()
    assert controller.reason != "emergency" and "emergency" not in controller.alarms


def test_manual_override_when_hot(controller, fan, store):
    store.update(mode="manual", manual_level=1)
    fan.temp = 76
    controller.tick()
    assert fan.level == 4 and controller.reason == "manual_override"
    assert "manual_override" in controller.alarms


def test_manual_level_is_applied_directly(controller, fan, store, clock):
    no_kick(store)
    store.update(mode="manual", manual_level=4)
    fan.temp = 40
    controller.tick()
    store.update(manual_level=1)
    clock.t += 1
    controller.tick()
    assert fan.level == 1


def test_sensor_failure_failsafe(controller, fan, store, clock):
    no_kick(store)
    fan.temp = 40
    controller.tick()
    assert fan.level == 0
    fan.fail = True
    for _ in range(SENSOR_FAILURE_TICKS):
        clock.t += 1
        controller.tick()
    assert fan.level == 4 and "sensor_failure" in controller.alarms
    fan.fail = False
    clock.t += 1
    controller.tick()
    assert "sensor_failure" not in controller.alarms


def test_first_tick_without_reading_is_full_speed(controller, fan):
    fan.fail = True
    controller.tick()
    assert fan.level == 4


def test_boost_and_cancel(controller, fan, store, clock):
    no_kick(store)
    fan.temp = 40
    controller.tick()
    controller.boost(30)
    assert fan.level == 4 and controller.reason == "boost"
    clock.t += 31
    controller.tick()
    assert controller.reason == "auto"
    controller.boost(30)
    assert controller.cancel_boost()
    assert controller.reason == "auto"


def test_boost_limits(controller):
    import pytest
    for bad in (0, BOOST_MAX_SECONDS + 1):
        with pytest.raises(ValueError):
            controller.boost(bad)


def test_spinup_kick(controller, fan, store, clock):
    fan.temp = 40
    controller.tick()
    fan.temp = 52
    clock.t += 1
    controller.tick()
    assert fan.level == 4 and controller.status()["reason"] == "spinup"
    clock.t += 3
    controller.tick()
    # The kick must not make the rate limiter step down slowly from full speed.
    assert fan.level == 1


def test_stall_detection(controller, fan, store, clock):
    no_kick(store)
    fan.auto_rpm = False
    fan.temp = 62
    controller.tick()
    clock.t += STALL_GRACE_SECONDS
    controller.tick()
    clock.t += STALL_SECONDS
    controller.tick()
    assert "fan_stall" in controller.alarms
    fan.auto_rpm = True
    clock.t += 1
    controller.tick()
    assert "fan_stall" not in controller.alarms


def test_external_change_is_reverted(controller, fan, store, clock):
    no_kick(store)
    fan.temp = 62
    controller.tick()
    fan.level = 0  # e.g. the kernel governor
    clock.t += 1
    controller.tick()
    assert fan.level == 2


def test_history_and_release(controller, fan, clock):
    controller.tick()
    clock.t += 3
    controller.tick()
    assert len(controller.history_since(0)) == 2
    controller.stop()
    assert fan.released
