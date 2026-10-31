from openpilot.cereal import log
from openpilot.common.constants import CV
try:
  from openpilot.common.realtime import DT_MDL
except ModuleNotFoundError:
  DT_MDL = 0.05
from openpilot.selfdrive.bronco_hub.curve_speed import is_road_edge_blocking_lane_change
from openpilot.selfdrive.controls.lib.bronco_config import BroncoHubConfig, CachedBroncoStore

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.
LANE_CHANGE_START_TIME = 0.5


def should_pause_lateral_on_blinker(carstate, config: BroncoHubConfig | None = None) -> bool:
  if config is None:
    return False
  one_blinker = bool(carstate.leftBlinker) != bool(carstate.rightBlinker)
  if not one_blinker:
    return False
  if config.blinker_mode == "pause":
    return True
  if config.pause_lateral_below_mph > 0.0 and carstate.vEgo < (config.pause_lateral_below_mph * CV.MPH_TO_MS):
    return True
  return False


class DesireHelper:
  def __init__(self):
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.auto_lc_timer = 0.0
    self.auto_lc_consumed = False
    self.prev_one_blinker = False
    self.desire = log.Desire.none
    self._store = CachedBroncoStore()

  @staticmethod
  def get_lane_change_direction(CS):
    return LaneChangeDirection.left if CS.leftBlinker else LaneChangeDirection.right

  def update(self, carstate, lateral_active, lane_change_prob, model_v2=None, config: BroncoHubConfig | None = None):
    cfg = config if config is not None else self._store.refresh()[0]
    v_ego = carstate.vEgo
    one_blinker = carstate.leftBlinker != carstate.rightBlinker
    below_lane_change_speed = v_ego < LANE_CHANGE_SPEED_MIN
    # Only fully cancelling the signal rearms automatic lane changes.
    if not carstate.leftBlinker and not carstate.rightBlinker:
      self.auto_lc_consumed = False

    if should_pause_lateral_on_blinker(carstate, cfg):
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
      self.lane_change_timer = 0.0
      self.auto_lc_timer = 0.0
      self.prev_one_blinker = one_blinker and lateral_active
      self.desire = log.Desire.none
      return

    if not lateral_active or self.lane_change_timer > LANE_CHANGE_TIME_MAX:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
      self.lane_change_timer = 0.0
      self.auto_lc_timer = 0.0
    else:
      if self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed:
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_timer = 0.0
        self.auto_lc_timer = 0.0
        # Initialize lane change direction to prevent UI alert flicker
        self.lane_change_direction = self.get_lane_change_direction(carstate)

      elif self.lane_change_state == LaneChangeState.preLaneChange:
        # Update lane change direction
        new_dir = self.get_lane_change_direction(carstate)
        if new_dir != self.lane_change_direction:
          self.auto_lc_timer = 0.0
        self.lane_change_direction = new_dir

        torque_applied = carstate.steeringPressed and \
                         ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                          (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right))

        blindspot_detected = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                              (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))

        road_edge_blocked = (
          cfg.road_edge_lane_change_block and
          model_v2 is not None and
          is_road_edge_blocking_lane_change(model_v2, self.lane_change_direction == LaneChangeDirection.left)
        )

        auto_triggered = False
        if cfg.blinker_mode == "auto" and not self.auto_lc_consumed and v_ego >= (cfg.auto_lane_change_min_mph * CV.MPH_TO_MS):
          if not blindspot_detected and not road_edge_blocked:
            self.auto_lc_timer += DT_MDL
            if self.auto_lc_timer >= cfg.auto_lane_change_delay_sec:
              auto_triggered = True
          else:
            self.auto_lc_timer = 0.0
        else:
          self.auto_lc_timer = 0.0

        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_timer = 0.0
          self.auto_lc_timer = 0.0
        elif (torque_applied or auto_triggered) and not blindspot_detected and not road_edge_blocked:
          self.auto_lc_consumed = True
          self.lane_change_state = LaneChangeState.laneChangeStarting
          self.lane_change_timer = 0.0
          self.auto_lc_timer = 0.0

      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        self.lane_change_timer += DT_MDL

        if lane_change_prob < 0.02 and self.lane_change_timer >= LANE_CHANGE_START_TIME:
          self.lane_change_timer = 0.0
          self.auto_lc_timer = 0.0
          if one_blinker:
            self.lane_change_state = LaneChangeState.preLaneChange
            self.lane_change_direction = self.get_lane_change_direction(carstate)
          else:
            self.lane_change_state = LaneChangeState.off
            self.lane_change_direction = LaneChangeDirection.none

    self.prev_one_blinker = one_blinker and lateral_active

    self.desire = log.Desire.none
    if self.lane_change_state == LaneChangeState.laneChangeStarting:
      if self.lane_change_direction == LaneChangeDirection.left:
        self.desire = log.Desire.laneChangeLeft
      elif self.lane_change_direction == LaneChangeDirection.right:
        self.desire = log.Desire.laneChangeRight
