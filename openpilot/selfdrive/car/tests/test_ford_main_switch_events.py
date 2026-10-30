"""Event-generation tests without loading device IPC or alert rendering libraries."""
import runpy
import sys
import pytest
from pathlib import Path
from types import SimpleNamespace

from openpilot.cereal import log
from opendbc.car import structs
from opendbc.car.ford.tests.test_bronco_non_acc import params


class EventCollector:
  # CarEvents only adds event IDs; actual alert rendering is out of scope.
  def __init__(self):
    self.names = []

  def add(self, name):
    self.names.append(name)


with pytest.MonkeyPatch.context() as mp:
  mp.setitem(sys.modules, 'openpilot.selfdrive.selfdrived.events', SimpleNamespace(Events=EventCollector))
  CarEvents = runpy.run_path(str(Path(__file__).parents[1] / 'car_events.py'))['CarEvents']

Event = log.OnroadEvent.EventName


class TestFordMainSwitchEvents:
  def test_standby_uses_one_shot_button_request_not_pcm(self):
    generator = CarEvents(params())
    prev = structs.CarState()
    cs = structs.CarState()
    cs.gearShifter = structs.CarState.GearShifter.drive
    cs.cruiseState.available = True
    cs.buttonEnable = True
    events = generator.create_common_events(cs, prev).names
    assert Event.buttonEnable in events
    assert Event.pcmDisable not in events
    assert Event.pcmEnable not in events
    cs.buttonEnable = False
    for active in (False, True, False, True):
      cs.cruiseState.enabled = active
      events = generator.create_common_events(cs, prev).names
      assert Event.buttonEnable not in events
      assert Event.pcmEnable not in events
      assert Event.pcmDisable not in events

  def test_cancel_and_off_disable(self):
    generator = CarEvents(params())
    cs = structs.CarState()
    cs.cruiseState.available = True
    cs.brakePressed = True
    cs.standstill = True
    assert Event.preEnableStandstill not in generator.create_common_events(cs, cs).names
    for pressed in (True, False):
      cs.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.cancel, pressed=pressed)]
      assert Event.buttonCancel in generator.create_common_events(cs, cs).names
    cs.buttonEvents = []
    cs.cruiseState.available = False
    assert Event.pcmDisable in generator.create_common_events(cs, cs).names

  def test_stock_variant_keeps_pcm_engagement(self):
    generator = CarEvents(params(radar=True))
    prev = structs.CarState()
    cs = structs.CarState()
    cs.cruiseState.available = True
    assert Event.pcmDisable in generator.create_common_events(cs, prev).names
    cs.cruiseState.enabled = True
    assert Event.pcmEnable in generator.create_common_events(cs, prev).names
