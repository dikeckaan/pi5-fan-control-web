import json

import pytest

from pifan.config import ConfigError, ConfigStore, validate, validate_curve


def test_creates_default_file(tmp_path):
    path = tmp_path / "sub" / "config.json"
    store = ConfigStore(str(path), 4)
    assert json.loads(path.read_text()) == store.get()
    assert store.get()["mode"] == "auto"


def test_v1_config_is_migrated(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"mode": "manual", "manual_level": 3,
                                "auto_thresholds": {"0": 0, "55": 2}}))
    cfg = ConfigStore(str(path), 4).get()
    assert cfg["mode"] == "manual" and cfg["manual_level"] == 3
    assert cfg["auto_thresholds"] == {"0": 0, "55": 2}
    assert cfg["safety"]["emergency_temp"] == 80


def test_corrupt_config_is_backed_up(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    store = ConfigStore(str(path), 4)
    assert store.get()["mode"] == "auto"
    assert list(tmp_path.glob("config.json.invalid-*"))


def test_defaults_respect_max_level(tmp_path):
    cfg = ConfigStore(str(tmp_path / "c.json"), 2).get()
    assert max(cfg["auto_thresholds"].values()) == 2


@pytest.mark.parametrize("curve,code", [
    ({}, "curve_empty"),
    ({"50": 5}, "curve_bad_level"),
    ({"abc": 1}, "curve_bad_temp"),
    ({"150": 1}, "curve_bad_temp"),
    ({"50": 3, "60": 1}, "curve_not_monotonic"),
    ({"50": True}, "curve_bad_level"),
    ({str(i): 0 for i in range(20)}, "curve_too_many"),
])
def test_bad_curves(curve, code):
    with pytest.raises(ConfigError) as e:
        validate_curve(curve, 4)
    assert e.value.code == code


def test_curve_is_sorted_and_normalised():
    assert list(validate_curve({"70": 4, "0": 0, "50": "1"}, 4).items()) == \
        [("0", 0), ("50", 1), ("70", 4)]


def test_override_cannot_exceed_emergency():
    with pytest.raises(ConfigError) as e:
        validate({"safety": {"emergency_temp": 70, "manual_override_temp": 80}}, 4)
    assert e.value.code == "override_above_emergency"


def test_failed_update_leaves_config_untouched(store):
    before = store.get()
    with pytest.raises(ConfigError):
        store.update(manual_level=9)
    assert store.get() == before
    assert json.loads(open(store.path).read()) == before
