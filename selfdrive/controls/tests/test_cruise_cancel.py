from types import SimpleNamespace

from opendbc.car.ford.values import FordFlags
from openpilot.selfdrive.controls.lib.cruise_cancel import should_cancel_cruise


def test_bronco_main_switch_does_not_cancel_stock_cruise_after_set():
  cp = SimpleNamespace(brand='ford', flags=FordFlags.NON_ACC | FordFlags.MAIN_SWITCH, pcmCruise=True)
  for openpilot_enabled in (False, True):
    assert not should_cancel_cruise(cp, cruise_enabled=True, openpilot_enabled=openpilot_enabled)


def test_other_cars_keep_existing_cancel_behavior():
  for brand, flags in (('ford', FordFlags.NON_ACC), ('toyota', 0)):
    cp = SimpleNamespace(brand=brand, flags=flags, pcmCruise=True)
    assert should_cancel_cruise(cp, cruise_enabled=True, openpilot_enabled=False)
    assert not should_cancel_cruise(cp, cruise_enabled=True, openpilot_enabled=True)
    assert not should_cancel_cruise(cp, cruise_enabled=False, openpilot_enabled=False)
  cp = SimpleNamespace(brand='ford', flags=0, pcmCruise=False)
  assert should_cancel_cruise(cp, cruise_enabled=True, openpilot_enabled=True)
