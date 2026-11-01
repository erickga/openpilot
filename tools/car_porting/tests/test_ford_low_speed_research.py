import math
import unittest
from dataclasses import replace

from tools.car_porting.ford_low_speed_research import Observation, TransitionStudy


class TestTransitionStudy(unittest.TestCase):
  def setUp(self):
    self.study = TransitionStudy()
    self.x = Observation(0.0, 20.0, 0.025, True)

  def settle(self, **kwargs):
    for i in range(41):
      self.x = replace(self.x, time=i * 0.05, **kwargs)
      result = self.study.update(self.x)
    return result

  def test_dwell_and_normal_slew(self):
    weights = []
    for i in range(40):
      weights.append(self.study.update(replace(self.x, time=i * .05)).weight)
    self.assertEqual(weights[:5], [0.] * 5)
    self.assertEqual(weights[-1], 1.)
    self.assertTrue(all(0 <= b - a <= .050001 for a, b in zip(weights, weights[1:], strict=False)))

  def test_speed_envelope_both_directions(self):
    for speed in (0, 9, 10, 30, 31, 80):
      self.study.reset()
      self.assertEqual(self.settle(kph=speed).weight, 0.)
    self.study.reset()
    self.assertAlmostEqual(self.settle(kph=29).weight, .2)
    self.assertEqual(self.study.update(replace(self.x, time=2.05, kph=30)).weight, 0.)

  def test_hard_exclusions_reset_and_require_new_dwell(self):
    for changes in ({"active": False}, {"fault": True}, {"valid": False},
                    {"driver_pressed": True}, {"lane_change": True}, {"curvature": math.nan},
                    {"kph": math.inf}, {"time": 5.0}, {"time": 1.0}):
      self.setUp()
      self.settle()
      self.assertEqual(self.study.update(replace(self.x, **({"time": 2.05} | changes))).weight, 0.)
      self.assertFalse(self.study.latched)

  def test_hysteresis_and_exit(self):
    self.settle()
    result = self.study.update(replace(self.x, time=2.05, curvature=.017))
    self.assertTrue(result.latched)
    for i in range(1, 31):
      result = self.study.update(replace(self.x, time=2.05+i*.05, curvature=.015))
    self.assertFalse(result.latched)
    self.assertEqual(result.weight, 0.)

  def test_sign_change_requires_fresh_dwell(self):
    self.settle()
    result = self.study.update(replace(self.x, time=2.05, curvature=-.025))
    self.assertEqual(result.weight, 0.)
    self.assertFalse(result.latched)

  def test_no_activation_for_small_turn(self):
    self.assertEqual(self.settle(curvature=.017).weight, 0.)


if __name__ == "__main__":
  unittest.main()
