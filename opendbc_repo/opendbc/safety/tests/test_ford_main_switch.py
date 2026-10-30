import random

from opendbc.car.ford.engagement import CANCEL_SIGNALS, MainSwitchEngagement
from opendbc.car.ford.values import FordSafetyFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_ford as ford


class TestFordMainSwitchSafety(ford.TestFordSafetyBase):
  STEER_MESSAGE = ford.MSG_LateralMotionControl
  TX_MSGS = ford.TestFordCANLateralOnlySafety.TX_MSGS
  RELAY_MALFUNCTION_ADDRS = ford.TestFordCANLateralOnlySafety.RELAY_MALFUNCTION_ADDRS
  FWD_BLACKLISTED_ADDRS = ford.TestFordCANLateralOnlySafety.FWD_BLACKLISTED_ADDRS
  PARAM = FordSafetyFlags.LATERAL_ONLY | FordSafetyFlags.MAIN_SWITCH

  def setUp(self):
    self.packer = CANPackerSafety('ford_lincoln_base_pt')
    self.safety = libsafety_py.libsafety
    self.reset()
    self._rx(self.buttons())
    self._rx(self.status(0))

  def reset(self, param=None):
    self.safety.set_safety_hooks(CarParams.SafetyModel.ford, self.PARAM if param is None else param)
    self.safety.init_tests()

  def status(self, state, brake=False, bus=0):
    return self.packer.make_can_msg_safety('EngBrakeData', bus, {
      'CcStat_D_Actl': state, 'BpedDrvAppl_D_Actl': 2 if brake else 1,
    })

  def buttons(self, signal=None, bus=0):
    return self.packer.make_can_msg_safety('Steering_Data_FD1', bus, {} if signal is None else {signal: 1})

  def test_acc_buttons(self):
    # Lateral engagement must not permit OP-generated stock speed resume.
    for allowed in (False, True):
      self.safety.set_controls_allowed(allowed)
      for bus in (0, 2):
        self.assertFalse(self._tx(self._acc_button_msg(ford.Buttons.RESUME, bus)))
      self.assertTrue(self._tx(self._acc_button_msg(ford.Buttons.TJA_TOGGLE, 2)))
    for engaged in (False, True):
      self._rx(self._pcm_status_msg(engaged))
      for bus in (0, 2):
        self.assertEqual(engaged, self._tx(self._acc_button_msg(ford.Buttons.CANCEL, bus)))

  def test_prev_user_brake(self, _user_brake_msg=None, get_brake_pressed_prev=None):
    # Main-switch lateral mode does not use generic brake disengagement.
    for pressed in (True, False, True):
      self._rx(self.status(3, pressed))
      self.assertFalse(self.safety.get_brake_pressed_prev())

  def test_allow_user_brake_at_zero_speed(self):
    # Braking at standstill keeps lateral controls allowed.
    self._rx(self._vehicle_moving_msg(0))
    self._rx(self.status(3, True))
    self.assertTrue(self.safety.get_controls_allowed())
    self._rx(self.status(3, False))
    self.assertTrue(self.safety.get_controls_allowed())
    self._rx(self.status(3, True))
    self.assertTrue(self.safety.get_controls_allowed())

  def test_startup_main_already_on(self):
    for state in (3, 4, 5):
      self.reset()
      self._rx(self.buttons())
      for _ in range(10):
        self._rx(self.status(state))
        self.assertFalse(self.safety.get_controls_allowed())
      self._rx(self.status(0))
      self._rx(self.status(state))
      self.assertTrue(self.safety.get_controls_allowed())

  def test_not_allow_user_brake_when_moving(self):
    # Braking while moving does not deactivate lateral control in main-switch mode.
    for speed in (self.STANDSTILL_THRESHOLD, self.STANDSTILL_THRESHOLD + 1, 10):
      self.setUp()
      self._rx(self._vehicle_moving_msg(speed))
      self._rx(self.status(3))
      self.assertTrue(self.safety.get_controls_allowed())
      self._rx(self.status(3, True))
      self.assertTrue(self.safety.get_controls_allowed())
      self._rx(self.status(3, False))
      self.assertTrue(self.safety.get_controls_allowed())

  def test_cancel_in_standby_and_active_never_resumes(self):
    for state in (3, 4, 5):
      for signal in CANCEL_SIGNALS:
        self.setUp()
        self._rx(self.status(state))
        self.assertTrue(self.safety.get_controls_allowed())
        self._rx(self.buttons(signal))
        self.assertFalse(self.safety.get_controls_allowed())
        self._rx(self.buttons())
        for next_state in (3, 4, 5, 3):
          self._rx(self.status(next_state))
          self.assertFalse(self.safety.get_controls_allowed())
        self._rx(self.status(0))
        self._rx(self.status(3))
        self.assertTrue(self.safety.get_controls_allowed())

  def test_brake_release_and_external_disable_never_resume(self):
    for moving in (0, 10):
      self.setUp()
      self._rx(self._vehicle_moving_msg(moving))
      self._rx(self.status(3))
      self.assertTrue(self.safety.get_controls_allowed())
      self._rx(self.buttons(CANCEL_SIGNALS[0]))
      self.assertFalse(self.safety.get_controls_allowed())
      self._rx(self.buttons())
      self._rx(self.status(3, True))
      self.assertFalse(self.safety.get_controls_allowed())
      for state in (3, 4, 5, 3):
        self._rx(self.status(state, False))
        self.assertFalse(self.safety.get_controls_allowed())
      self._rx(self.status(0))
      self._rx(self.status(3))
      self.assertTrue(self.safety.get_controls_allowed())
      self.safety.set_controls_allowed(False)
      for state in (3, 4, 5):
        self._rx(self.status(state))
        self.assertFalse(self.safety.get_controls_allowed())

  def test_blocked_edge_consumed(self):
    for block in ('gas', 'cancel', 'missing_buttons'):
      self.reset()
      if block != 'missing_buttons':
        self._rx(self.buttons())
      self._rx(self.status(0))
      if block == 'gas':
        self._rx(self._user_gas_msg(1))
      if block == 'cancel':
        self._rx(self.buttons(CANCEL_SIGNALS[0]))
      self._rx(self.status(3))
      self.assertFalse(self.safety.get_controls_allowed())
      self._rx(self.buttons())
      self._rx(self._user_gas_msg(0))
      self._rx(self.status(3))
      self.assertFalse(self.safety.get_controls_allowed())

  def test_invalid_states_and_wrong_bus_cannot_enable(self):
    for state in (1, 2, 6, 7):
      self.setUp()
      self._rx(self.status(state))
      self._rx(self.status(3))
      self.assertFalse(self.safety.get_controls_allowed())
    self.setUp()
    self._rx(self.status(3, bus=2))
    self.assertFalse(self.safety.get_controls_allowed())

  def test_missing_buttons_timeout_disables(self):
    self._rx(self.status(3))
    self.assertTrue(self.safety.get_controls_allowed())
    self.safety.set_timer(2_000_000)
    # Refresh every stock monitored message, but withhold Steering_Data_FD1.
    for msg in (self.status(3), self._speed_msg(0), self._speed_msg_2(0), self._yaw_rate_msg(0, 1),
                self._user_gas_msg(0), self._vehicle_moving_msg(0)):
      self._rx(msg)
    self.safety.safety_tick()
    self.assertFalse(self.safety.get_controls_allowed())
    self._rx(self.buttons())
    self._rx(self.status(3))
    self.assertFalse(self.safety.get_controls_allowed())

  def test_no_longitudinal_and_invalid_mode_combinations(self):
    for param in (self.PARAM, self.PARAM | FordSafetyFlags.LONG_CONTROL, self.PARAM | FordSafetyFlags.CANFD, FordSafetyFlags.MAIN_SWITCH):
      self.reset(param)
      self.safety.set_controls_allowed(True)
      msg = self.packer.make_can_msg_safety('ACCDATA', 0, {'AccPrpl_A_Rq': 1, 'AccBrkTot_A_Rq': -1})
      self.assertFalse(self._tx(msg))
      if param != self.PARAM:
        self.assertFalse(self._tx(self._lat_ctl_msg(False, 0, 0, 0, 0)))

  def test_python_and_panda_agree_on_driver_sequences(self):
    self.reset()
    logic = MainSwitchEngagement()
    expected = False
    rng = random.Random(73002)
    for _ in range(3000):
      state = rng.randrange(8)
      brake, gas, cancel = (rng.randrange(5) == 0 for _ in range(3))
      self._rx(self.buttons(CANCEL_SIGNALS[0] if cancel else None))
      self._rx(self._user_gas_msg(1 if gas else 0))
      self._rx(self.status(state, brake))
      if logic.update(state, brake, gas, cancel, True):
        expected = True
      if state not in (3, 4, 5) or cancel:
        expected = False
      self.assertEqual(expected, self.safety.get_controls_allowed(), (state, brake, gas, cancel))
