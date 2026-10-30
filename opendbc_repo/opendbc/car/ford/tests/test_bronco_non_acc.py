"""Regression tests for non-ACC identification and fail-closed engagement."""
from collections import defaultdict
from types import SimpleNamespace
import unittest

from opendbc.car import Bus, structs, gen_empty_fingerprint
from opendbc.car.ford.carstate import CarState
from opendbc.car.ford.carcontroller import CarController
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.values import CAR, DBC, Ecu, FordFlags, FordSafetyFlags, FW_QUERY_CONFIG
from opendbc.car.ford.fingerprints import FW_VERSIONS


def fw(ecu, version, request=()):
  return structs.CarParams.CarFw(ecu=ecu, fwVersion=version, request=list(request))


def params(*, candidate=CAR.FORD_BRONCO_SPORT_MK1, radar=False, abs_version=b'LX6C-2D053-KE',
           config=b'\x00' * 7 + b'\xff\xff' + b'\x00' * 15, alpha_long=False, bsm=False):
  firmware = [fw(Ecu.abs, abs_version.ljust(24, b'\x00')), fw(Ecu.shiftByWire, b'gear')]
  if radar:
    firmware.append(fw(Ecu.fwdRadar, b'radar'))
  if config is not None:
    firmware.append(fw(Ecu.eps, config, [b'\x22\xde\x01']))
  fingerprint = gen_empty_fingerprint()
  if bsm:
    fingerprint[0].update({0x3A6: 8, 0x3A7: 8})
  return CarInterface.get_params(candidate, fingerprint, firmware, alpha_long, False, False)


class TestBroncoNonACC(unittest.TestCase):
  def test_blind_spot_detection_does_not_select_main_switch_mode(self):
    for radar in (False, True):
      for bsm in (False, True):
        cp = params(radar=radar, bsm=bsm)
        self.assertEqual(bool(cp.flags & FordFlags.HAS_BSM), bsm)
        self.assertEqual(bool(cp.flags & FordFlags.NON_ACC), not radar)
        self.assertEqual(bool(cp.flags & FordFlags.MAIN_SWITCH), not radar)
        self.assertEqual(cp.openpilotLongitudinalControl, radar)
        cs = CarState(cp)
        pt = SimpleNamespace(can_valid=True, vl=defaultdict(lambda: defaultdict(int)))
        cam = SimpleNamespace(can_valid=True, vl=defaultdict(lambda: defaultdict(int)))
        pt.vl['Side_Detect_L_Stat']['SodDetctLeft_D_Stat'] = 1
        pt.vl['Side_Detect_R_Stat']['SodDetctRight_D_Stat'] = 1
        result = cs.update({Bus.pt: pt, Bus.cam: cam})
        self.assertEqual(result.leftBlindspot, bsm)
        self.assertEqual(result.rightBlindspot, bsm)

  def test_longitudinal_cannot_be_enabled(self):
    for alpha_long in (False, True):
      cp = params(alpha_long=alpha_long)
      self.assertTrue(cp.flags & FordFlags.NON_ACC)
      self.assertTrue(cp.radarUnavailable)
      self.assertFalse(cp.openpilotLongitudinalControl)
      self.assertFalse(cp.alphaLongitudinalAvailable)
      self.assertEqual(cp.safetyConfigs[-1].safetyParam, FordSafetyFlags.LATERAL_ONLY | FordSafetyFlags.MAIN_SWITCH)
      self.assertFalse(cp.dashcamOnly)

  def test_missing_disabled_or_malformed_pscm_blocks_control(self):
    for config in (None, b'', b'\xff' * 23, b'\xff' * 25, bytes(24),
                   b'\x00' * 7 + b'\xff\x00' + bytes(15), b'\x00' * 7 + b'\x00\xff' + bytes(15)):
      with self.subTest(config=config):
        self.assertTrue(params(config=config).dashcamOnly)

  def test_missing_radar_does_not_identify_unknown_variant(self):
    cp = params(abs_version=b'LX6C-2D053-RD')
    self.assertFalse(cp.flags & FordFlags.NON_ACC)
    self.assertTrue(cp.dashcamOnly)

  def test_acc_bronco_and_other_ford_keep_stock_mode(self):
    for cp in (params(radar=True), params(candidate=CAR.FORD_ESCAPE_MK4, radar=True)):
      self.assertFalse(cp.flags & FordFlags.NON_ACC)
      self.assertTrue(cp.openpilotLongitudinalControl)
      self.assertFalse(cp.safetyConfigs[-1].safetyParam & FordSafetyFlags.LATERAL_ONLY)

  def test_radar_optional_only_for_known_non_acc_fuzzy_match(self):
    for candidate in (CAR.FORD_BRONCO_SPORT_MK1, CAR.FORD_ESCAPE_MK4):
      offline = FW_VERSIONS[candidate]
      live = {key[1:]: {versions[0]} for key, versions in offline.items() if key[0] != Ecu.fwdRadar}
      matches = FW_QUERY_CONFIG.match_fw_to_car_fuzzy(live, '', {candidate: offline})
      self.assertEqual(matches, {candidate} if candidate == CAR.FORD_BRONCO_SPORT_MK1 else set())
      live[(0x760, None)] = {b'LX6C-2D053-RD'.ljust(24, b'\x00')}
      self.assertEqual(FW_QUERY_CONFIG.match_fw_to_car_fuzzy(live, '', {candidate: offline}), set())

  def test_carstate_reports_real_cruise_state_and_brake(self):
    cp = params()
    cs = CarState(cp)
    pt = SimpleNamespace(can_valid=True, vl=defaultdict(lambda: defaultdict(int)))
    cam = SimpleNamespace(can_valid=True, vl=defaultdict(lambda: defaultdict(int)))
    for state in range(8):
      pt.vl['EngBrakeData']['CcStat_D_Actl'] = state
      pt.vl['EngBrakeData']['BpedDrvAppl_D_Actl'] = 2
      result = cs.update({Bus.pt: pt, Bus.cam: cam})
      self.assertEqual(result.cruiseState.enabled, state in (4, 5))
      self.assertTrue(result.brakePressed)
      self.assertFalse(result.cruiseState.nonAdaptive)

  def test_controller_sends_no_acceleration_and_keeps_cancel(self):
    cp = params(alpha_long=True)
    cs = CarState(cp)
    pt = SimpleNamespace(can_valid=True, vl=defaultdict(lambda: defaultdict(int)))
    cam = SimpleNamespace(can_valid=True, vl=defaultdict(lambda: defaultdict(int)))
    cs.out = cs.update({Bus.pt: pt, Bus.cam: cam})
    controller = CarController(DBC[cp.carFingerprint], cp)
    cc = structs.CarControl()
    cc.cruiseControl.cancel = True
    sent = []
    for frame in range(100):
      _, msgs = controller.update(cc.as_reader(), cs, frame * 10_000_000)
      sent.extend(msgs)
    self.assertNotIn(0x186, [msg[0] for msg in sent])
    self.assertIn(0x83, [msg[0] for msg in sent])

    # A lateral resume request must never press RES on the stock speed controller.
    cc.cruiseControl.cancel = False
    cc.cruiseControl.resume = True
    cc.enabled = True
    for frame in range(100, 200):
      _, msgs = controller.update(cc.as_reader(), cs, frame * 10_000_000)
      self.assertNotIn(0x83, [msg[0] for msg in msgs])


if __name__ == '__main__':
  unittest.main()
