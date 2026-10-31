import pyray as rl
from dataclasses import dataclass
from openpilot.common.constants import CV
from openpilot.selfdrive.bronco_hub.curve_speed import CurveSpeedEstimate, estimate_upcoming_curve
from openpilot.selfdrive.controls.lib.bronco_config import BroncoHubConfig, BroncoNavState, CachedBroncoStore
from openpilot.selfdrive.ui.onroad.exp_button import ExpButton
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

# Constants
SET_SPEED_NA = 255
KM_TO_MILE = 0.621371
CRUISE_DISABLED_CHAR = '–'


@dataclass(frozen=True)
class UIConfig:
  header_height: int = 300
  border_size: int = 30
  button_size: int = 192
  set_speed_width_metric: int = 200
  set_speed_width_imperial: int = 172
  set_speed_height: int = 204
  wheel_icon_size: int = 144


@dataclass(frozen=True)
class FontSizes:
  current_speed: int = 176
  speed_unit: int = 66
  max_speed: int = 40
  set_speed: int = 90


@dataclass(frozen=True)
class Colors:
  WHITE = rl.WHITE
  DISENGAGED = rl.Color(145, 155, 149, 255)
  OVERRIDE = rl.Color(145, 155, 149, 255)  # Added
  ENGAGED = rl.Color(128, 216, 166, 255)
  DISENGAGED_BG = rl.Color(0, 0, 0, 153)
  OVERRIDE_BG = rl.Color(145, 155, 149, 204)
  ENGAGED_BG = rl.Color(128, 216, 166, 204)
  GREY = rl.Color(166, 166, 166, 255)
  DARK_GREY = rl.Color(114, 114, 114, 255)
  BLACK_TRANSLUCENT = rl.Color(0, 0, 0, 166)
  WHITE_TRANSLUCENT = rl.Color(255, 255, 255, 200)
  BORDER_TRANSLUCENT = rl.Color(255, 255, 255, 75)
  HEADER_GRADIENT_START = rl.Color(0, 0, 0, 114)
  HEADER_GRADIENT_END = rl.BLANK


UI_CONFIG = UIConfig()
FONT_SIZES = FontSizes()
COLORS = Colors()


class HudRenderer(Widget):
  def __init__(self):
    super().__init__()
    """Initialize the HUD renderer."""
    self.is_cruise_set: bool = False
    self.is_cruise_available: bool = True
    self.set_speed: float = SET_SPEED_NA
    self.speed: float = 0.0
    self.v_ego_cluster_seen: bool = False

    self._font_semi_bold: rl.Font = gui_app.font(FontWeight.SEMI_BOLD)
    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
    self._font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)

    self._exp_button: ExpButton = ExpButton(UI_CONFIG.button_size, UI_CONFIG.wheel_icon_size)
    self._bronco_store = CachedBroncoStore()
    self._bronco_cfg = BroncoHubConfig()
    self._nav_state = BroncoNavState()
    self._curve_est = CurveSpeedEstimate()

  def _update_state(self) -> None:
    """Update HUD state based on car state and controls state."""
    self._bronco_cfg, self._nav_state = self._bronco_store.refresh()
    sm = ui_state.sm
    if sm.recv_frame["carState"] < ui_state.started_frame:
      self.is_cruise_set = False
      self.set_speed = SET_SPEED_NA
      self.speed = 0.0
      self._curve_est = CurveSpeedEstimate()
      return

    controls_state = sm['controlsState']
    car_state = sm['carState']

    if self._bronco_cfg.curve_speed_warning and sm.recv_frame["modelV2"] >= ui_state.started_frame:
      self._curve_est = estimate_upcoming_curve(
        sm['modelV2'],
        car_state.vEgo,
        self._bronco_cfg.curve_max_lat_accel,
      )
    else:
      self._curve_est = CurveSpeedEstimate()

    v_cruise_cluster = car_state.vCruiseCluster
    self.set_speed = (
      controls_state.deprecated.vCruise if v_cruise_cluster == 0.0 else v_cruise_cluster
    )
    self.is_cruise_set = 0 < self.set_speed < SET_SPEED_NA
    self.is_cruise_available = self.set_speed != -1

    if self.is_cruise_set and not ui_state.is_metric:
      self.set_speed *= KM_TO_MILE

    v_ego_cluster = car_state.vEgoCluster
    self.v_ego_cluster_seen = self.v_ego_cluster_seen or v_ego_cluster != 0.0
    v_ego = v_ego_cluster if self.v_ego_cluster_seen else car_state.vEgo
    speed_conversion = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    self.speed = max(0.0, v_ego * speed_conversion)

  def _render(self, rect: rl.Rectangle) -> None:
    """Render HUD elements to the screen."""
    # Draw the header background
    rl.draw_rectangle_gradient_v(
      int(rect.x),
      int(rect.y),
      int(rect.width),
      UI_CONFIG.header_height,
      COLORS.HEADER_GRADIENT_START,
      COLORS.HEADER_GRADIENT_END,
    )

    if self.is_cruise_available:
      self._draw_set_speed(rect)

    self._draw_current_speed(rect)
    self._draw_bronco_overlays(rect)

    button_x = rect.x + rect.width - UI_CONFIG.border_size - UI_CONFIG.button_size
    button_y = rect.y + UI_CONFIG.border_size
    self._exp_button.render(rl.Rectangle(button_x, button_y, UI_CONFIG.button_size, UI_CONFIG.button_size))

  def user_interacting(self) -> bool:
    return self._exp_button.is_pressed

  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    """Draw the MAX speed indicator box."""
    set_speed_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + 60 + (UI_CONFIG.set_speed_width_imperial - set_speed_width) // 2
    y = rect.y + 45

    set_speed_rect = rl.Rectangle(x, y, set_speed_width, UI_CONFIG.set_speed_height)
    rl.draw_rectangle_rounded(set_speed_rect, 0.35, 10, COLORS.BLACK_TRANSLUCENT)
    rl.draw_rectangle_rounded_lines_ex(set_speed_rect, 0.35, 10, 6, COLORS.BORDER_TRANSLUCENT)

    max_color = COLORS.GREY
    set_speed_color = COLORS.DARK_GREY
    if self.is_cruise_set:
      set_speed_color = COLORS.WHITE
      if ui_state.status == UIStatus.ENGAGED:
        max_color = COLORS.ENGAGED
      elif ui_state.status == UIStatus.DISENGAGED:
        max_color = COLORS.DISENGAGED
      elif ui_state.status == UIStatus.OVERRIDE:
        max_color = COLORS.OVERRIDE

    max_text = tr("MAX")
    max_text_width = measure_text_cached(self._font_semi_bold, max_text, FONT_SIZES.max_speed).x
    rl.draw_text_ex(
      self._font_semi_bold,
      max_text,
      rl.Vector2(x + (set_speed_width - max_text_width) / 2, y + 27),
      FONT_SIZES.max_speed,
      0,
      max_color,
    )

    set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.set_speed))
    speed_text_width = measure_text_cached(self._font_bold, set_speed_text, FONT_SIZES.set_speed).x
    rl.draw_text_ex(
      self._font_bold,
      set_speed_text,
      rl.Vector2(x + (set_speed_width - speed_text_width) / 2, y + 77),
      FONT_SIZES.set_speed,
      0,
      set_speed_color,
    )

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
    """Draw the current vehicle speed and unit."""
    speed_text = str(round(self.speed))
    speed_text_size = measure_text_cached(self._font_bold, speed_text, FONT_SIZES.current_speed)
    speed_pos = rl.Vector2(rect.x + rect.width / 2 - speed_text_size.x / 2, 180 - speed_text_size.y / 2)
    rl.draw_text_ex(self._font_bold, speed_text, speed_pos, FONT_SIZES.current_speed, 0, COLORS.WHITE)

    unit_text = tr("km/h") if ui_state.is_metric else tr("mph")
    unit_text_size = measure_text_cached(self._font_medium, unit_text, FONT_SIZES.speed_unit)
    unit_pos = rl.Vector2(rect.x + rect.width / 2 - unit_text_size.x / 2, 290 - unit_text_size.y / 2)
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, FONT_SIZES.speed_unit, 0, COLORS.WHITE_TRANSLUCENT)

  def _draw_bronco_overlays(self, rect: rl.Rectangle) -> None:
    """Draw OSM speed limit badge, upcoming navigation turn banner, and curve speed warning."""
    speed_conv = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    unit_label = "km/h" if ui_state.is_metric else "mph"
    nav = self._nav_state

    # 1. OSM Speed Limit Badge (next to MAX speed box)
    if self._bronco_cfg.hud_show_nav and nav.has_fresh_speed_limit():
      limit_val = round(nav.speed_limit_mps * speed_conv)
      box_x = rect.x + 255
      box_y = rect.y + 45
      box_w, box_h = 156, 204
      limit_rect = rl.Rectangle(box_x, box_y, box_w, box_h)
      over_limit = self.speed > (limit_val + (5 if ui_state.is_metric else 3))
      bg_col = rl.Color(190, 35, 35, 220) if over_limit else rl.Color(245, 245, 245, 230)
      fg_col = COLORS.WHITE if over_limit else rl.Color(20, 20, 20, 255)
      rl.draw_rectangle_rounded(limit_rect, 0.3, 10, bg_col)
      rl.draw_rectangle_rounded_lines_ex(limit_rect, 0.3, 10, 5, rl.Color(30, 30, 30, 200))

      lbl = "LIMIT"
      lbl_w = measure_text_cached(self._font_semi_bold, lbl, 34).x
      rl.draw_text_ex(self._font_semi_bold, lbl, rl.Vector2(box_x + (box_w - lbl_w) / 2, box_y + 24), 34, 0, fg_col)

      val_str = str(limit_val)
      val_w = measure_text_cached(self._font_bold, val_str, 84).x
      rl.draw_text_ex(self._font_bold, val_str, rl.Vector2(box_x + (box_w - val_w) / 2, box_y + 78), 84, 0, fg_col)

    # 2. Upcoming Navigation Turn Banner
    if self._bronco_cfg.hud_show_nav and nav.is_fresh():
      dist_m = max(0.0, float(nav.distance_to_next_m))
      if ui_state.is_metric:
        dist_str = f"{dist_m / 1000.0:.1f} km" if dist_m >= 1000 else f"{int(round(dist_m / 10.0) * 10)} m"
      else:
        feet = dist_m * 3.28084
        dist_str = f"{dist_m * 0.000621371:.1f} mi" if feet >= 1500 else f"{int(round(feet / 50.0) * 50)} ft"

      mod = (nav.next_maneuver_modifier or nav.next_maneuver_type or "").lower()
      arrow = "↑"
      if "left" in mod:
        arrow = "←"
      elif "right" in mod:
        arrow = "→"
      elif "uturn" in mod:
        arrow = "↶"

      street = nav.next_street_name or nav.next_maneuver_instruction or nav.destination_name or "Route"
      if len(street) > 24:
        street = street[:22] + ".."
      banner_text = f"{arrow} {dist_str} • {street}"

      bx, by = rect.x + 60, rect.y + 268
      bw = max(360, measure_text_cached(self._font_semi_bold, banner_text, 42).x + 48)
      bh = 82
      banner_rect = rl.Rectangle(bx, by, bw, bh)
      near_turn = dist_m < 220.0
      banner_bg = rl.Color(22, 127, 64, 225) if near_turn else COLORS.BLACK_TRANSLUCENT
      rl.draw_rectangle_rounded(banner_rect, 0.35, 10, banner_bg)
      rl.draw_rectangle_rounded_lines_ex(banner_rect, 0.35, 10, 4, COLORS.BORDER_TRANSLUCENT)
      rl.draw_text_ex(self._font_semi_bold, banner_text, rl.Vector2(bx + 24, by + 20), 42, 0, COLORS.WHITE)

    # Steering pause takes priority over the advisory curve banner.
    paused = ui_state.blinker_steering_paused
    if paused or (self._bronco_cfg.curve_speed_warning and self._curve_est.warning_active):
      rec_spd = round(self._curve_est.recommended_speed_mps * speed_conv)
      alert_txt = "STEERING PAUSED • TAKE CONTROL" if paused else f"CURVE AHEAD • SLOW TO {rec_spd} {unit_label.upper()}"
      tw = measure_text_cached(self._font_bold, alert_txt, 44).x
      pw = tw + 64
      ph = 84
      px = rect.x + (rect.width - pw) / 2
      py = rect.y + 345
      pill_rect = rl.Rectangle(px, py, pw, ph)
      rl.draw_rectangle_rounded(pill_rect, 0.45, 10, rl.Color(215, 85, 15, 235))
      rl.draw_rectangle_rounded_lines_ex(pill_rect, 0.45, 10, 5, COLORS.WHITE)
      rl.draw_text_ex(self._font_bold, alert_txt, rl.Vector2(px + 32, py + 20), 44, 0, COLORS.WHITE)
