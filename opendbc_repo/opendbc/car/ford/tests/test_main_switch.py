import unittest
from collections import defaultdict
from types import SimpleNamespace

from opendbc.car import Bus, structs
from opendbc.car.ford.carstate import CarState
from opendbc.car.ford.engagement import CANCEL_SIGNALS, MainSwitchEngagement
from opendbc.car.ford.tests.test_bronco_non_acc import params
from opendbc.car.ford.values import FordFlags
from opendbc.car.interfaces import CarInterfaceBase


class TestMainSwitch(unittest.TestCase):
  def setUp(self):
    self.request = MainSwitchEngagement()

  def update(self, state, **kwargs):
    return self.request.update(state, **(dict(brake=False, gas=False, cancel=False, valid=True) | kwargs))

  def test_startup_and_recovery_never_enable(self):
    for initial in (3, 4, 5):
      self.setUp()
      for _ in range(10):
        self.assertFalse(self.update(initial))
      self.assertFalse(self.update(0))
      self.assertTrue(self.update(initial))
      self.assertFalse(self.update(initial))

  def test_inhibited_edge_is_consumed(self):
    for block in ('gas', 'cancel', 'valid'):
      self.setUp()
      self.update(0)
      options = dict(brake=False, gas=False, cancel=False, valid=True)
      options[block] = block != 'valid'
      self.assertFalse(self.request.update(3, **options))
      for state in (3, 4, 5, 3):
        self.assertFalse(self.update(state))
      self.update(0)
      self.assertTrue(self.update(3))

  def test_brake_does_not_inhibit_or_rearm(self):
    self.update(0)
    self.assertTrue(self.update(3, brake=True))
    for state in (3, 4, 5, 3):
      self.assertFalse(self.update(state, brake=True))
      self.assertFalse(self.update(state, brake=False))
    self.assertFalse(self.update(3, brake=True, cancel=True))
    self.assertFalse(self.update(3, brake=False))

  def test_fault_state_is_not_off(self):
    for state in (1, 2, 6, 7):
      self.update(0)
      self.assertFalse(self.update(state))
      self.assertFalse(self.update(3))

  def test_carstate_preserves_pcm_state_and_reports_cancel(self):
    cp = params()
    cs = CarState(cp)
    pt = SimpleNamespace(can_valid=True, vl=defaultdict(lambda: defaultdict(int)))
    cam = SimpleNamespace(can_valid=True, vl=defaultdict(lambda: defaultdict(int)))

    def update(state):
      pt.vl['EngBrakeData']['CcStat_D_Actl'] = state
      return cs.update({Bus.pt: pt, Bus.cam: cam})

    self.assertFalse(update(3).buttonEnable)
    update(0)
    result = update(3)
    self.assertTrue(result.buttonEnable)
    self.assertFalse(any(b.type == structs.CarState.ButtonEvent.Type.cancel for b in result.buttonEvents))
    self.assertFalse(result.cruiseState.enabled)  # standby is still standby
    self.assertFalse(update(5).buttonEnable)  # SET does not re-enable lateral
    self.assertTrue(update(5).cruiseState.enabled)
    for signal in CANCEL_SIGNALS:
      pt.vl['Steering_Data_FD1'][signal] = 1
      self.assertTrue(any(b.type == structs.CarState.ButtonEvent.Type.cancel and b.pressed for b in update(3).buttonEvents))
      pt.vl['Steering_Data_FD1'][signal] = 0
      self.assertFalse(update(3).buttonEnable)
    # Parser failure consumes the OFF edge; reception resuming cannot enable.
    update(0)
    pt.can_valid = False
    self.assertFalse(update(3).buttonEnable)
    pt.can_valid = True
    self.assertFalse(update(3).buttonEnable)

  def test_other_variants_do_not_select_mode(self):
    self.assertTrue(params().flags & FordFlags.MAIN_SWITCH)
    self.assertFalse(params(radar=True).flags & FordFlags.MAIN_SWITCH)
    self.assertFalse(params(abs_version=b'unknown').flags & FordFlags.MAIN_SWITCH)

  def test_interface_preserves_main_switch_enable_request(self):
    ret = structs.CarState(buttonEnable=True)
    state = SimpleNamespace(update=lambda parsers: ret, update_button_enable=lambda events: False,
                            out=structs.CarState(), cluster_speed_hyst_gap=0., cluster_min_speed=0.)
    interface = SimpleNamespace(CS=state, can_parsers={}, v_ego_cluster_seen=False)
    self.assertTrue(CarInterfaceBase.update(interface, []).buttonEnable)
    ret.buttonEnable = False
    self.assertFalse(CarInterfaceBase.update(interface, []).buttonEnable)
