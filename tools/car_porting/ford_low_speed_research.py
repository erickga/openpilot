"""Offline transition eligibility experiment. Never produces or transmits CAN commands.

The weight is a research signal, NOT a calibrated actuator blend. A mapping between
curvature and Ford path angle has not been established for the non-ACC Bronco.
"""
import argparse
import csv
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass(frozen=True)
class Observation:
  time: float
  kph: float
  curvature: float
  active: bool
  driver_pressed: bool = False
  valid: bool = True
  fault: bool = False
  lane_change: bool = False


@dataclass(frozen=True)
class Decision:
  weight: float
  reason: str
  latched: bool


class TransitionStudy:
  """Hysteresis/dwell experiment with an explicit hard eligibility envelope.

  All constants are experimental scheduling values, not validated steering limits.
  Disengagement, faults, override and invalid data reset immediately. Thus weight
  continuity is NOT guaranteed at hard exclusions, nor is it actuator continuity.
  """
  def __init__(self):
    self.reset()

  def reset(self):
    self.weight = 0.0
    self.latched = False
    self.dwell = 0.0
    self.previous_time = None
    self.direction = 0

  def update(self, x: Observation) -> Decision:
    finite = all(math.isfinite(v) for v in (x.time, x.kph, x.curvature))
    dt = 0.0 if self.previous_time is None else x.time - self.previous_time
    reason = None
    if not finite or not x.valid:
      reason = "invalid_input"
    elif dt < 0 or dt > 0.15:
      reason = "time_discontinuity"
    elif not x.active or x.fault:
      reason = "inactive_or_fault"
    elif x.driver_pressed or x.lane_change:
      reason = "driver_or_lane_change"
    elif not 10.0 < x.kph < 30.0:
      reason = "outside_speed_envelope"
    if reason:
      self.reset()
      self.previous_time = x.time if finite else None
      return Decision(0.0, reason, False)

    self.previous_time = x.time
    direction = 1 if x.curvature > 0 else -1 if x.curvature < 0 else 0
    if direction != self.direction:
      self.weight = 0.0
      self.latched = False
      self.dwell = 0.0
      self.direction = direction
      dt = 0.0

    demand = abs(x.curvature)
    if demand <= 0.016:
      self.latched = False
      self.dwell = 0.0
    elif not self.latched:
      self.dwell = self.dwell + dt if demand >= 0.018 else 0.0
      self.latched = self.dwell >= 0.25

    # Fade in from 10–15 km/h and out from 25–30 km/h; exactly zero at 30.
    speed_weight = max(0.0, min(1.0, (x.kph - 10.0) / 5.0, (30.0 - x.kph) / 5.0))
    demand_weight = max(0.0, min(1.0, (demand - 0.016) / 0.004))
    target = speed_weight * demand_weight if self.latched else 0.0
    step = dt  # experimental maximum 1 weight-unit/s for normal transitions
    self.weight += max(-step, min(step, target - self.weight))
    # Envelope takes precedence over slew continuity during rapid speed changes.
    self.weight = min(self.weight, speed_weight)
    return Decision(self.weight, "candidate" if self.weight > 0 else "stock", self.latched)


def replay(path: Path, output: Path) -> dict:
  study = TransitionStudy()
  summary = {"samples": 0, "candidate_samples": 0, "max_weight": 0.0,
             "can_transmission": False, "actuator_mapping_validated": False}
  with path.open() as src, output.open("w") as dst:
    writer = csv.DictWriter(dst, fieldnames=["time", "kph", "curvature", "weight", "reason", "latched"])
    writer.writeheader()
    for row in csv.DictReader(src):
      x = Observation(float(row["elapsed"]), float(row["kph"]), float(row["controller_request"]),
                      row["lat_active"] == "1", row["driver_pressed"] == "1", row["can_valid"] == "1",
                      row["steer_temp"] != "0" or row["steer_perm"] != "0",
                      row["left_blinker"] != "0" or row["right_blinker"] != "0")
      decision = study.update(x)
      writer.writerow({"time": x.time, "kph": x.kph, "curvature": x.curvature, **asdict(decision)})
      summary["samples"] += 1
      summary["candidate_samples"] += int(decision.weight > 0)
      summary["max_weight"] = max(summary["max_weight"], decision.weight)
  return summary


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("csv", type=Path)
  parser.add_argument("--output", required=True, type=Path)
  args = parser.parse_args()
  if args.csv.resolve() == args.output.resolve():
    parser.error("output must differ from input")
  print(json.dumps(replay(args.csv, args.output), indent=2))
