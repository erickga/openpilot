"""Explicit OFF -> ON engagement for the experimental non-ACC Bronco port."""

# Dedicated cancel / combined cancel-resume / off inputs. A combined cancel-resume
# input is deliberately treated as cancellation only, never as a resume request.
CANCEL_SIGNALS = ("CcAslButtnCnclPress", "CcAslButtnCnclResPress", "CcAslButtnOffCnclPress", "CcButtnOffPress")


class MainSwitchEngagement:
  def __init__(self):
    self.off_seen = False

  def update(self, cruise_state: int, brake: bool, gas: bool, cancel: bool, valid: bool) -> bool:
    if not valid:
      self.off_seen = False
      return False
    enable = self.off_seen and cruise_state in (3, 4, 5) and not (gas or cancel)
    # Consume the request even if gas or cancel prevents engagement. Releasing
    # that input must never generate a delayed enable event.
    self.off_seen = cruise_state == 0 and not cancel
    return enable
