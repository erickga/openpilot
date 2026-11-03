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
    usable = s.speed >= 15/3.6 and 4 < s.distance <= 80 and s.confidence >= .96 and closing >= 3.0
    ttc = s.distance/closing if closing > 0 else math.inf
    # Relative deceleration needed to remove closing speed before a 4m buffer,
    # assuming constant relative acceleration. Require a meaningful deceleration
    # demand (>= 2.0 m/s^2) and allow progressive driver braking before warning.
    required = closing**2/(2*max(s.distance-4, 1))
    recovery = s.lead_accel-s.accel
    hazard = usable and ttc <= 3.5 and required >= 2.0 and recovery < 0.75*required
    # Hysteresis prevents noisy camera velocity from repeatedly restarting dwell.
    hold = usable and ttc <= 4.2 and required >= 1.6 and recovery < 0.95*required and self.since is not None
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
    if hazard and self.armed and s.now-self.since >= .45 and s.now-self.last_alert >= 10:
      self.last_alert = s.now
      self.until = s.now+2
      self.armed = False
    visible = s.now < self.until
    return visible, visible and s.now-self.last_alert < .3
