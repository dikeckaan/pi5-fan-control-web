import pytest

from pifan.hardware import HardwareError, SysfsFan


def make_sysfs(root, policy="step_wise", temp="52300"):
    zone = root / "thermal_zone0"
    zone.mkdir()
    (zone / "temp").write_text(temp + "\n")
    (zone / "policy").write_text(policy + "\n")
    (zone / "available_policies").write_text("step_wise user_space\n")
    cdev = root / "cooling_device0"
    cdev.mkdir()
    (cdev / "type").write_text("pwm-fan\n")
    (cdev / "cur_state").write_text("0\n")
    (cdev / "max_state").write_text("4\n")
    return zone, cdev


def test_reads_and_writes(tmp_path):
    zone, cdev = make_sysfs(tmp_path)
    fan = SysfsFan(root=str(tmp_path), hwmon_globs=())
    assert fan.read_temp() == 52.3
    fan.write_level(9)  # clamped
    assert (cdev / "cur_state").read_text() == "4"
    assert fan.read_rpm() is None


def test_implausible_temperature(tmp_path):
    make_sysfs(tmp_path, temp="999000")
    with pytest.raises(HardwareError):
        SysfsFan(root=str(tmp_path), hwmon_globs=()).read_temp()


def test_policy_handover(tmp_path):
    zone, cdev = make_sysfs(tmp_path)
    fan = SysfsFan(root=str(tmp_path), hwmon_globs=())
    assert fan.take_control()
    assert (zone / "policy").read_text() == "user_space"
    fan.release_control()
    assert (zone / "policy").read_text() == "step_wise"
    assert (cdev / "cur_state").read_text() == "4"


def test_leftover_userspace_policy_is_restored(tmp_path):
    zone, _ = make_sysfs(tmp_path, policy="user_space")
    fan = SysfsFan(root=str(tmp_path), hwmon_globs=())
    assert fan.take_control()
    fan.release_control()
    assert (zone / "policy").read_text() == "step_wise"
