"""Early driver advisory only. No actuator or stock-FCW output.

Camera lead estimates are not ground truth. Thresholds are initial tuning values,
replayed against an observed closing approach, not validated collision guarantees.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ClosingSample:
  now: float
  speed: float
  accel: float
  distance: float
  relative_speed: float
  lead_accel: float
  confidence: float
  valid: bool = True
  brake_pressed: bool = False  # diagnostic only: pedal contact is not enough
  urgent: bool = False


class ClosingAdvisory:
  def __init__(self):
    self.previous = None
    self.since = None
    self.until = -math.inf
    self.last_alert = -math.inf
    self.safe_since = None
    self.armed = True

  def update(self, sample: ClosingSample) -> tuple[bool, bool]:
    s = sample
    finite = all(math.isfinite(v) for v in (s.now, s.speed, s.accel, s.distance,
                                           s.relative_speed, s.lead_accel, s.confidence))
    prev = self.previous
    self.previous = s if finite else None
    continuous = prev is None or (0 < s.now-prev.now <= .25 and
                                  abs(s.distance-prev.distance) <= 5 + abs(prev.relative_speed)*(s.now-prev.now))
    if not finite or not s.valid or s.urgent or not continuous:
      self.since = None
      self.until = -math.inf
      self.safe_since = None
      return False, False

    closing = -s.relative_speed
    usable = s.speed >= 10/3.6 and 3 < s.distance <= 100 and s.confidence >= .95 and closing >= 2
    ttc = s.distance/closing if closing > 0 else math.inf
    # Relative deceleration needed to remove closing speed before a 5m buffer,
    # assuming constant relative acceleration. This is NOT a stopping guarantee.
    required = closing**2/(2*max(s.distance-5, 1))
    recovery = s.lead_accel-s.accel
    hazard = usable and ttc <= 5.5 and recovery < required
    # Hysteresis prevents noisy camera velocity from repeatedly restarting dwell.
    hold = usable and ttc <= 6.5 and recovery < 1.2*required and self.since is not None
    if not hazard and not hold:
      self.since = None
      self.until = -math.inf
      if self.safe_since is None:
        self.safe_since = s.now
      if s.now-self.safe_since >= 2:
        self.armed = True
      return False, False

    self.safe_since = None
    if self.since is None:
      self.since = s.now
    if hazard and self.armed and s.now-self.since >= .3 and s.now-self.last_alert >= 10:
      self.last_alert = s.now
      self.until = s.now+2
      self.armed = False
    visible = s.now < self.until
    return visible, visible and s.now-self.last_alert < .3
