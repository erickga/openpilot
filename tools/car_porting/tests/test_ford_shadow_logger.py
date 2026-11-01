import json
import math
from pathlib import Path
import tempfile
import unittest

from tools.car_porting.ford_low_speed_research import Observation
from tools.car_porting.ford_shadow_logger import Journal, ShadowModel, clean, decode_lmc


class TestShadow(unittest.TestCase):
  def test_wire_decode_neutral_and_extremes(self):
    # Independent bit construction, raw curvature 2000 = +.02, angle 1000 = 0.
    data = bytes([250, 0, 0, 125, 4, 0, 0, 0])
    self.assertEqual(decode_lmc(data), {"curvature_wire": .02, "path_angle_wire_rad": 0., "mode": 1})
    self.assertAlmostEqual(decode_lmc(bytes(8))["curvature_wire"], -.02)

  def test_transition_records_hypothetical_commands(self):
    model = ShadowModel()
    outputs = [model.update(Observation(i*.05, 20, .03, True), .02) for i in range(50)]
    self.assertEqual(outputs[0]["shadow_curvature_internal"], .02)
    self.assertEqual(outputs[-1]["shadow_curvature_internal"], 0)
    self.assertAlmostEqual(outputs[-1]["shadow_path_angle_internal_rad"], .15)
    self.assertTrue(all(not o["transmitted"] and not o["mapping_validated"] for o in outputs))

  def test_exclusions_clear_angle(self):
    for observation in (Observation(3, 30, .03, True), Observation(3, 20, .03, False),
                        Observation(3, 20, .03, True, True), Observation(3, 20, math.nan, True)):
      model = ShadowModel()
      for i in range(60):
        model.update(Observation(i*.05, 20, .03, True), .02)
      self.assertEqual(model.update(observation, .02)["shadow_path_angle_internal_rad"], 0)

  def test_bad_actual_output_is_not_invented(self):
    result = ShadowModel().update(Observation(0, 20, .03, True), math.nan)
    self.assertIsNone(result["shadow_curvature_wire"])
    self.assertEqual(result["decision"]["reason"], "invalid_input")

  def test_sign_symmetry(self):
    values = []
    for sign in (-1, 1):
      model = ShadowModel()
      for i in range(50):
        result = model.update(Observation(i*.05, 20, sign*.03, True), sign*.02)
      values.append(result["shadow_path_angle_wire_rad"])
    self.assertEqual(values[0], -values[1])

  def test_journal_rotation_permissions_and_lock(self):
    with tempfile.TemporaryDirectory() as tmp:
      directory = Path(tmp)
      journal = Journal(directory, max_bytes=700, backups=2)
      try:
        with self.assertRaises(BlockingIOError):
          Journal(directory)
        for i in range(50):
          journal.write({"sample": i, "bad": math.nan})
      finally:
        journal.close()
      files = list(directory.glob("shadow.jsonl*"))
      self.assertLessEqual(len(files), 3)
      for file in files:
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertLessEqual(file.stat().st_size, 700)
        for line in file.read_text().splitlines():
          self.assertIsInstance(json.loads(line), dict)

  def test_nonfinite_json_is_null(self):
    self.assertEqual(clean({"v": [math.inf, math.nan, 2.]}), {"v": [None, None, 2.]})


if __name__ == "__main__":
  unittest.main()
