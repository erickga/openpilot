import math
from dataclasses import replace
import unittest

from openpilot.selfdrive.selfdrived.closing_advisory import ClosingAdvisory, ClosingSample


class TestClosingAdvisory(unittest.TestCase):
  def sample(self, now, **changes):
    return replace(ClosingSample(now, 48.072/3.6, -.84977, 35.0992, -7.0079, -.87519, .9928,
                                 brake_pressed=True), **changes)

  def test_recorded_braking_approach_warns_after_persistence(self):
    detector = ClosingAdvisory()
    self.assertEqual(detector.update(self.sample(0)), (False, False))
    for i in range(1, 6):
      self.assertEqual(detector.update(self.sample(i*.05)), (False, False))
    self.assertEqual(detector.update(self.sample(.31)), (True, True))
    for i in range(7, 42):
      visible, chime = detector.update(self.sample(i*.05))
    self.assertTrue(visible)
    self.assertFalse(chime)

  def test_adequate_braking_and_opening_gap_do_not_warn(self):
    for changes in ({"accel": -3.0}, {"relative_speed": 1.0}, {"distance": 70.0},
                    {"confidence": .9}, {"valid": False}, {"urgent": True},
                    {"speed": 1.0}, {"lead_accel": math.nan}):
      d = ClosingAdvisory()
      for i in range(30):
        self.assertEqual(d.update(self.sample(i*.05, **changes)), (False, False))

  def test_no_repeated_chime_during_continuous_hazard(self):
    d = ClosingAdvisory()
    rising = 0
    previous = False
    for i in range(600):
      _, chime = d.update(self.sample(i*.05))
      rising += chime and not previous
      previous = chime
    self.assertEqual(rising, 1)

  def test_urgent_or_stale_clears_immediately(self):
    for changes in ({"urgent": True}, {"valid": False}):
      d = ClosingAdvisory()
      for i in range(15):
        d.update(self.sample(i*.05))
      self.assertEqual(d.update(self.sample(.75, **changes)), (False, False))

  def test_cut_in_requires_new_persistence(self):
    d = ClosingAdvisory()
    for i in range(6):
      d.update(self.sample(i*.05))
    self.assertEqual(d.update(self.sample(.31, distance=20)), (False, False))
    self.assertEqual(d.update(self.sample(.36, distance=20)), (False, False))

  def test_rearms_after_safe_period_and_cooldown(self):
    d = ClosingAdvisory()
    for i in range(20):
      d.update(self.sample(i*.05))
    for i in range(20, 240):
      d.update(self.sample(i*.05, relative_speed=1))
    outcomes = [d.update(self.sample(i*.05)) for i in range(240, 260)]
    self.assertTrue(any(chime for _, chime in outcomes))


if __name__ == '__main__':
  unittest.main()
