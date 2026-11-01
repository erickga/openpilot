from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from tools.car_porting.ford_shadow_service import is_driving, prune, new_session


class TestService(unittest.TestCase):
  def test_fresh_valid_started_required(self):
    self.assertTrue(is_driving(True, True, True, .1))
    for args in ((False, True, True, 0), (True, False, True, 0), (True, True, False, 0),
                 (True, True, True, 6), (True, True, True, -1)):
      self.assertFalse(is_driving(*args))

  def test_retention_keeps_ten_and_unrelated_data(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      (root / "drive-unrelated").mkdir()
      for i in range(12):
        d = root / f"drive-{i:03d}"
        d.mkdir()
        (d / "session.json").write_text(json.dumps({"sequence": i+1}))
      prune(root, root / "drive-011")
      self.assertFalse((root / "drive-000").exists())
      self.assertTrue((root / "drive-002").exists())
      self.assertTrue((root / "drive-unrelated").exists())
      self.assertEqual(len(list(root.glob("*/session.json"))), 10)

  def test_byte_limit_preserves_active_drive(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      for i in range(3):
        d = root / f"drive-{i}"
        d.mkdir()
        (d / "session.json").write_text(json.dumps({"sequence": i+1}))
        (d / "shadow.jsonl").write_text("x" * 100)
      with patch("tools.car_porting.ford_shadow_service.MAX_BYTES", 150):
        prune(root, root / "drive-2")
      self.assertEqual(list(root.iterdir()), [root / "drive-2"])

  def test_clock_rollback_cannot_reorder_sessions(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      with patch("tools.car_porting.ford_shadow_service.time.time_ns", return_value=200):
        first = new_session(root)
      with patch("tools.car_porting.ford_shadow_service.time.time_ns", return_value=100):
        second = new_session(root)
      with patch("tools.car_porting.ford_shadow_service.MAX_DRIVES", 1):
        prune(root, second)
      self.assertFalse(first.exists())
      self.assertTrue(second.exists())
      self.assertNotEqual(new_session(root), second)

  def test_legacy_without_sequence_is_protected(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      legacy = root / "drive-0"
      legacy.mkdir()
      (legacy / "session.json").write_text("{}")
      with patch("tools.car_porting.ford_shadow_service.MAX_BYTES", 0):
        prune(root)
      self.assertTrue(legacy.exists())


if __name__ == "__main__":
  unittest.main()
