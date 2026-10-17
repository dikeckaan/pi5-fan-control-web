import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pifan.config import ConfigStore  # noqa: E402
from pifan.controller import FanController  # noqa: E402
from pifan.hardware import HardwareError  # noqa: E402


class FakeFan:
    max_state = 4

    def __init__(self):
        self.temp = 40.0
        self.level = 0
        self.rpm = 0
        self.fail = False
        self.auto_rpm = True
        self.writes = []
        self.released = False

    def read_temp(self):
        if self.fail:
            raise HardwareError("boom")
        return self.temp

    def read_level(self):
        return self.level

    def write_level(self, level):
        self.level = level
        self.writes.append(level)

    def read_rpm(self):
        return self.level * 1000 if self.auto_rpm else self.rpm

    def take_control(self):
        return True

    def release_control(self):
        self.released = True
        self.level = self.max_state


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def fan():
    return FakeFan()


@pytest.fixture
def store(tmp_path, fan):
    return ConfigStore(str(tmp_path / "config.json"), fan.max_state)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def controller(fan, store, clock):
    return FanController(fan, store, clock=clock, wall_clock=clock)


def no_kick(store):
    store.update(safety={"spinup_kick": False})
