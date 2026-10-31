import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = "/data/bronco_hub_config.json" if os.path.isdir("/data") else "/tmp/bronco_hub_config.json"
DEFAULT_NAV_STATE_PATH = "/data/bronco_nav_state.json" if os.path.isdir("/data") else "/tmp/bronco_nav_state.json"

VALID_BLINKER_MODES = ("nudge", "auto", "pause")


@dataclass
class BroncoHubConfig:
  blinker_mode: str = "nudge"  # "nudge" | "auto" | "pause"
  auto_lane_change_delay_sec: float = 0.6
  auto_lane_change_min_mph: float = 25.0
  pause_lateral_below_mph: float = 0.0
  blindspot_path_glow: bool = True
  road_edge_lane_change_block: bool = True
  curve_speed_warning: bool = True
  curve_max_lat_accel: float = 2.2
  hud_show_nav: bool = True
  nav_assist_lane_change: bool = False

  def normalized(self) -> "BroncoHubConfig":
    mode = self.blinker_mode if self.blinker_mode in VALID_BLINKER_MODES else "nudge"
    delay = float(max(0.2, min(2.5, float(self.auto_lane_change_delay_sec))))
    min_mph = float(max(15.0, min(65.0, float(self.auto_lane_change_min_mph))))
    pause_mph = float(max(0.0, min(45.0, float(self.pause_lateral_below_mph))))
    max_lat = float(max(1.4, min(3.5, float(self.curve_max_lat_accel))))
    return BroncoHubConfig(
      blinker_mode=mode,
      auto_lane_change_delay_sec=round(delay, 2),
      auto_lane_change_min_mph=round(min_mph, 1),
      pause_lateral_below_mph=round(pause_mph, 1),
      blindspot_path_glow=bool(self.blindspot_path_glow),
      road_edge_lane_change_block=bool(self.road_edge_lane_change_block),
      curve_speed_warning=bool(self.curve_speed_warning),
      curve_max_lat_accel=round(max_lat, 2),
      hud_show_nav=bool(self.hud_show_nav),
      nav_assist_lane_change=bool(self.nav_assist_lane_change),
    )


@dataclass
class BroncoNavState:
  active: bool = False
  destination_name: str = ""
  dest_lat: float = 0.0
  dest_lon: float = 0.0
  distance_remaining_m: float = 0.0
  duration_remaining_s: float = 0.0
  next_maneuver_type: str = ""
  next_maneuver_modifier: str = ""
  next_maneuver_instruction: str = ""
  next_street_name: str = ""
  distance_to_next_m: float = 0.0
  recommended_turn_speed_mps: float = 0.0
  speed_limit_mps: float = 0.0
  speed_limit_updated_at: float = 0.0
  nav_desire: str = "none"
  updated_at: float = 0.0

  def is_fresh(self, now: float | None = None, max_age_s: float = 15.0) -> bool:
    if not self.active or self.updated_at <= 0.0:
      return False
    cur = time.monotonic() if now is None else now
    return 0.0 <= (cur - self.updated_at) <= max_age_s

  def has_fresh_speed_limit(self, now: float | None = None) -> bool:
    cur = time.monotonic() if now is None else now
    return self.speed_limit_mps > 0.0 and self.speed_limit_updated_at > 0.0 and 0.0 <= cur - self.speed_limit_updated_at <= 30.0


def get_config_path() -> Path:
  return Path(os.environ.get("BRONCO_HUB_CONFIG_PATH", DEFAULT_CONFIG_PATH))


def get_nav_state_path() -> Path:
  return Path(os.environ.get("BRONCO_HUB_NAV_STATE_PATH") or os.environ.get("BRONCO_NAV_STATE_PATH") or DEFAULT_NAV_STATE_PATH)


def _atomic_write_json(path: Path, payload: dict) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
      json.dump(payload, f, indent=2, sort_keys=True)
      f.flush()
    os.replace(tmp_path, path)
  except Exception:
    try:
      os.unlink(tmp_path)
    except OSError:
      pass
    raise


def load_bronco_config(path: Path | None = None) -> BroncoHubConfig:
  target = path or get_config_path()
  try:
    if not target.is_file():
      return BroncoHubConfig()
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
      return BroncoHubConfig()
    allowed = {k: data[k] for k in BroncoHubConfig.__dataclass_fields__ if k in data}
    return BroncoHubConfig(**allowed).normalized()
  except Exception:
    return BroncoHubConfig()


def save_bronco_config(config: BroncoHubConfig | dict, path: Path | None = None) -> BroncoHubConfig:
  target = path or get_config_path()
  current = load_bronco_config(target)
  if isinstance(config, dict):
    merged = asdict(current)
    for k in BroncoHubConfig.__dataclass_fields__:
      if k in config:
        merged[k] = config[k]
    norm = BroncoHubConfig(**merged).normalized()
  else:
    norm = config.normalized()
  _atomic_write_json(target, asdict(norm))
  return norm


def load_nav_state(path: Path | None = None) -> BroncoNavState:
  target = path or get_nav_state_path()
  try:
    if not target.is_file():
      return BroncoNavState()
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
      return BroncoNavState()
    allowed = {k: data[k] for k in BroncoNavState.__dataclass_fields__ if k in data}
    return BroncoNavState(**allowed)
  except Exception:
    return BroncoNavState()


def save_nav_state(state: BroncoNavState | dict, path: Path | None = None) -> BroncoNavState:
  target = path or get_nav_state_path()
  if isinstance(state, dict):
    allowed = {k: state[k] for k in BroncoNavState.__dataclass_fields__ if k in state}
    if "updated_at" not in allowed:
      allowed["updated_at"] = time.monotonic()
    nav = BroncoNavState(**allowed)
  else:
    nav = state
    if nav.updated_at <= 0.0:
      nav.updated_at = time.monotonic()
  _atomic_write_json(target, asdict(nav))
  return nav


class CachedBroncoStore:
  """Low-overhead reader for realtime loops (checks mtime at most every poll_interval_s)."""

  def __init__(self, poll_interval_s: float = 0.25, config_path: Path | None = None, nav_path: Path | None = None):
    self.poll_interval_s = poll_interval_s
    self._config_path = config_path
    self._nav_path = nav_path
    self._last_check_mono = 0.0
    self._config_mtime = -1.0
    self._nav_mtime = -1.0
    self.config = BroncoHubConfig()
    self.nav_state = BroncoNavState()

  def refresh(self, now_mono: float | None = None) -> tuple[BroncoHubConfig, BroncoNavState]:
    cur = time.monotonic() if now_mono is None else now_mono
    if (cur - self._last_check_mono) < self.poll_interval_s:
      return self.config, self.nav_state
    self._last_check_mono = cur

    c_path = self._config_path or get_config_path()
    try:
      c_mtime = c_path.stat().st_mtime
    except OSError:
      c_mtime = -1.0
    if c_mtime != self._config_mtime:
      self._config_mtime = c_mtime
      self.config = load_bronco_config(c_path)

    n_path = self._nav_path or get_nav_state_path()
    try:
      n_mtime = n_path.stat().st_mtime
    except OSError:
      n_mtime = -1.0
    if n_mtime != self._nav_mtime:
      self._nav_mtime = n_mtime
      self.nav_state = load_nav_state(n_path)

    return self.config, self.nav_state
