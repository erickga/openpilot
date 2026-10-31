import math
import re
import time
from pathlib import Path

from openpilot.common.constants import CV
from openpilot.selfdrive.bronco_hub.curve_speed import estimate_upcoming_curve
from openpilot.selfdrive.controls.lib.bronco_config import load_bronco_config, load_nav_state

DEFAULT_REALDATA_DIR = "/data/media/0/realdata"
BRONCO_TIRE_RADIUS_M = 0.362  # 225/65R17 nominal rolling radius

EXTENDED_FORD_CAN_MESSAGES = [
  ("WheelSpeed", float("nan")),
  ("Tire_Pressure_Data_FD1", float("nan")),
  ("EngVehicleSpThrottle", float("nan")),
  ("BrakeSnData_4", float("nan")),
  ("PowertrainData_10", float("nan")),
  ("Yaw_Data_FD1", float("nan")),
  ("EPAS_INFO", float("nan")),
  ("SteeringPinion_Data", float("nan")),
  ("Side_Detect_L_Stat", float("nan")),
  ("Side_Detect_R_Stat", float("nan")),
  ("BodyInfo_3_FD1", float("nan")),
  ("EngBrakeData", float("nan")),
  ("BrakeSysFeatures", float("nan")),
]

_SEGMENT_DIR_RE = re.compile(r"^([0-9a-fA-F_]+--[0-9a-fA-F]+)--(\d+)$")
ALLOWED_SEGMENT_FILES = (
  "fcamera.hevc",
  "ecamera.hevc",
  "dcamera.hevc",
  "qcamera.ts",
  "rlog.zst",
  "rlog.bz2",
  "qlog.zst",
  "qlog.bz2",
)


def list_recorded_routes(realdata_dir: str | Path = DEFAULT_REALDATA_DIR, limit: int = 25) -> list[dict]:
  """Scan /data/media/0/realdata and group segments by route."""
  root = Path(realdata_dir)
  if not root.is_dir():
    return []

  routes: dict[str, dict] = {}
  try:
    entries = list(root.iterdir())
  except OSError:
    return []

  for entry in entries:
    if not entry.is_dir():
      continue
    m = _SEGMENT_DIR_RE.match(entry.name)
    if not m:
      continue
    route_id, seg_str = m.group(1), m.group(2)
    seg_num = int(seg_str)
    try:
      mtime = entry.stat().st_mtime
    except OSError:
      mtime = 0.0

    files = []
    total_bytes = 0
    for fname in ALLOWED_SEGMENT_FILES:
      fpath = entry / fname
      if fpath.is_file():
        try:
          sz = fpath.stat().st_size
        except OSError:
          sz = 0
        files.append({"name": fname, "size_bytes": sz})
        total_bytes += sz

    info = routes.setdefault(
      route_id,
      {
        "route_id": route_id,
        "latest_mtime": mtime,
        "segment_count": 0,
        "total_size_mb": 0.0,
        "segments": [],
      },
    )
    info["latest_mtime"] = max(info["latest_mtime"], mtime)
    info["segment_count"] += 1
    info["total_size_mb"] = round(info["total_size_mb"] + (total_bytes / (1024 * 1024)), 2)
    info["segments"].append(
      {
        "segment": seg_num,
        "dir_name": entry.name,
        "mtime": mtime,
        "files": files,
      }
    )

  sorted_routes = sorted(routes.values(), key=lambda r: r["latest_mtime"], reverse=True)[:limit]
  for r in sorted_routes:
    r["segments"].sort(key=lambda s: s["segment"])
  return sorted_routes


def resolve_segment_file(realdata_dir: str | Path, route_id: str, segment: int, filename: str) -> Path | None:
  """Safely resolve a dashcam/log file inside realdata_dir without path traversal."""
  if filename not in ALLOWED_SEGMENT_FILES:
    return None
  if not re.match(r"^[0-9a-fA-F_]+--[0-9a-fA-F]+$", route_id):
    return None
  root = Path(realdata_dir).resolve()
  target = (root / f"{route_id}--{int(segment)}" / filename).resolve()
  try:
    target.relative_to(root)
  except ValueError:
    return None
  return target if target.is_file() else None


class BroncoTelemetryCollector:
  """Collects live Cereal and extended Ford CAN signals for Bronco Hub."""

  def __init__(self, enable_ipc: bool = True):
    self.enable_ipc = enable_ipc
    self.sm = None
    self.cp_pt = None
    self.can_sock = None
    self.params = None
    self._last_gps = {"lat": 0.0, "lon": 0.0, "bearing_deg": 0.0, "speed_mps": 0.0, "has_fix": False}
    self._extended_can: dict = {
      "rpm": 0.0,
      "throttle_pct": 0.0,
      "brake_torque_nm": 0.0,
      "gear_num": 0,
      "wheel_speeds_kph": {"fl": 0.0, "fr": 0.0, "rl": 0.0, "rr": 0.0},
      "tire_press_kpa": {"lf": 0.0, "rf": 0.0, "lr": 0.0, "rr": 0.0},
      "eps_voltage_v": 0.0,
      "eps_current_a": 0.0,
      "driver_torque_nm": 0.0,
      "pinion_angle_deg": 0.0,
      "pinion_qf": 3,
      "roll_rate_deg_s": 0.0,
      "yaw_rate_deg_s": 0.0,
      "cta_left": False,
      "cta_right": False,
      "hood_open": False,
      "tailgate_open": False,
      "service_required": False,
      "cc_stat": 0,
    }
    if enable_ipc:
      self._init_ipc()

  def _init_ipc(self) -> None:
    try:
      from openpilot.cereal import messaging
      from openpilot.common.params import Params
      from opendbc.can import CANParser

      self.params = Params()
      self.sm = messaging.SubMaster(
        [
          "carState",
          "carControl",
          "carOutput",
          "controlsState",
          "selfdriveState",
          "modelV2",
          "pandaStates",
          "deviceState",
          "gpsLocationExternal",
          "vehicleParameters",
          "livePose",
        ]
      )
      self.can_sock = messaging.sub_sock("can", conflate=False)
      self.cp_pt = CANParser("ford_lincoln_base_pt", EXTENDED_FORD_CAN_MESSAGES, 0)
    except Exception:
      self.sm = None
      self.cp_pt = None

  def config_changes_allowed(self) -> bool:
    """Fail closed unless fresh device/Panda state confirms ignition is off."""
    if self.sm is None:
      return False
    now = time.monotonic()
    for service in ("deviceState", "pandaStates"):
      if not self.sm.seen[service] or not self.sm.valid[service] or not 0 <= now - self.sm.recv_time[service] < 2.0:
        return False
    pandas = self.sm["pandaStates"]
    return not self.sm["deviceState"].started and bool(len(pandas)) and not any(p.ignitionLine or p.ignitionCan for p in pandas)

  def _update_extended_can(self, can_msgs) -> None:
    if self.cp_pt is None or not can_msgs:
      return
    try:
      strings = []
      for m in can_msgs:
        frames = [(c.address, c.dat, c.src) for c in m.can]
        strings.append((m.logMonoTime, frames))
      self.cp_pt.update(strings)
      vl = self.cp_pt.vl

      # Wheel angular velocity (rad/s) -> linear kph
      rad_to_kph = BRONCO_TIRE_RADIUS_M * 3.6
      ws = vl["WheelSpeed"]
      self._extended_can["wheel_speeds_kph"] = {
        "fl": round(float(ws.get("WhlFl_W_Meas", 0.0)) * rad_to_kph, 1),
        "fr": round(float(ws.get("WhlFr_W_Meas", 0.0)) * rad_to_kph, 1),
        "rl": round(float(ws.get("WhlRl_W_Meas", 0.0)) * rad_to_kph, 1),
        "rr": round(float(ws.get("WhlRr_W_Meas", 0.0)) * rad_to_kph, 1),
      }

      tp = vl["Tire_Pressure_Data_FD1"]
      self._extended_can["tire_press_kpa"] = {
        "lf": round(float(tp.get("Tire_Press_LF_Data", 0.0)), 1),
        "rf": round(float(tp.get("Tire_Press_RF_Data", 0.0)), 1),
        "lr": round(float(tp.get("Tire_Press_LR_OLR_Data", 0.0)), 1),
        "rr": round(float(tp.get("Tire_Press_RR_ORR_Data", 0.0)), 1),
      }

      th = vl["EngVehicleSpThrottle"]
      self._extended_can["throttle_pct"] = round(float(th.get("ApedPos_Pc_ActlArb", 0.0)), 1)
      self._extended_can["rpm"] = round(float(th.get("EngAout_N_Actl", 0.0)), 0)

      brk = vl["BrakeSnData_4"]
      self._extended_can["brake_torque_nm"] = round(float(brk.get("BrkTot_Tq_Actl", 0.0)), 1)

      pt = vl["PowertrainData_10"]
      self._extended_can["gear_num"] = int(pt.get("GearPos_D_Actl", 0))

      yaw = vl["Yaw_Data_FD1"]
      self._extended_can["yaw_rate_deg_s"] = round(math.degrees(float(yaw.get("VehYaw_W_Actl", 0.0))), 2)
      self._extended_can["roll_rate_deg_s"] = round(math.degrees(float(yaw.get("VehRol_W_Actl", 0.0))), 2)

      epas = vl["EPAS_INFO"]
      self._extended_can["eps_voltage_v"] = round(float(epas.get("SteMdule_U_Meas", 0.0)), 2)
      self._extended_can["eps_current_a"] = round(float(epas.get("SteMdule_I_Est", 0.0)), 2)
      self._extended_can["driver_torque_nm"] = round(float(epas.get("SteeringColumnTorque", 0.0)), 2)

      pin = vl["SteeringPinion_Data"]
      self._extended_can["pinion_angle_deg"] = round(float(pin.get("StePinComp_An_Est", 0.0)), 2)
      self._extended_can["pinion_qf"] = int(pin.get("StePinCompAnEst_D_Qf", 3))

      sdl = vl["Side_Detect_L_Stat"]
      sdr = vl["Side_Detect_R_Stat"]
      self._extended_can["cta_left"] = bool(sdl.get("CtaAlrtLeft_D_Stat", 0))
      self._extended_can["cta_right"] = bool(sdr.get("CtaAlrtRight_D_Stat", 0))

      body = vl["BodyInfo_3_FD1"]
      self._extended_can["hood_open"] = bool(body.get("DrStatHood_B_Actl", 0))
      self._extended_can["tailgate_open"] = bool(body.get("DrStatTgate_B_Actl", 0) or body.get("DrStatInnrTgate_B_Actl", 0))
      self._extended_can["service_required"] = bool(body.get("BodySrvcRqd_B_Rq", 0))

      eb = vl["EngBrakeData"]
      self._extended_can["cc_stat"] = int(eb.get("CcStat_D_Actl", 0))
    except Exception:
      pass

  def update_and_snapshot(self) -> dict:
    cfg = load_bronco_config()
    nav = load_nav_state()
    if not nav.has_fresh_speed_limit():
      nav.speed_limit_mps = 0.0

    if self.sm is not None:
      try:
        from openpilot.cereal import messaging

        can_Gd = messaging.drain_sock(self.can_sock) if self.can_sock is not None else []
        if can_Gd:
          self._update_extended_can(can_Gd)
        self.sm.update(0)
      except Exception:
        pass

    cs = self.sm["carState"] if (self.sm is not None and self.sm.seen.get("carState", False)) else None
    cc = self.sm["carControl"] if (self.sm is not None and self.sm.seen.get("carControl", False)) else None
    ss = self.sm["selfdriveState"] if (self.sm is not None and self.sm.seen.get("selfdriveState", False)) else None
    ds = self.sm["deviceState"] if (self.sm is not None and self.sm.seen.get("deviceState", False)) else None
    vp = self.sm["vehicleParameters"] if (self.sm is not None and self.sm.seen.get("vehicleParameters", False)) else None
    lp = self.sm["livePose"] if (self.sm is not None and self.sm.seen.get("livePose", False)) else None
    mv2 = self.sm["modelV2"] if (self.sm is not None and self.sm.seen.get("modelV2", False)) else None
    gps = self.sm["gpsLocationExternal"] if (self.sm is not None and self.sm.seen.get("gpsLocationExternal", False)) else None
    ps_list = self.sm["pandaStates"] if (self.sm is not None and self.sm.seen.get("pandaStates", False)) else []

    if gps is not None and getattr(gps, "hasFix", False):
      self._last_gps = {
        "lat": round(float(gps.latitude), 6),
        "lon": round(float(gps.longitude), 6),
        "bearing_deg": round(float(gps.bearingDeg), 1),
        "speed_mps": round(float(gps.speed), 2),
        "has_fix": True,
      }

    v_ego = float(cs.vEgo) if cs is not None else 0.0
    steer_deg = float(cs.steeringAngleDeg) if cs is not None else 0.0
    steer_tq = float(cs.steeringTorque) if cs is not None else 0.0
    yaw_rate = float(cs.yawRate) if cs is not None else 0.0
    lat_g = (v_ego * yaw_rate) / 9.80665

    roll_deg = math.degrees(float(vp.roll)) if vp is not None else 0.0
    pitch_deg = 0.0
    if lp is not None and hasattr(lp, "orientationNED") and len(lp.orientationNED.xyz) >= 2:
      pitch_deg = math.degrees(float(lp.orientationNED.xyz[1]))
      if abs(roll_deg) < 1e-4:
        roll_deg = math.degrees(float(lp.orientationNED.xyz[0]))

    curve_est = estimate_upcoming_curve(mv2, v_ego, cfg.curve_max_lat_accel) if mv2 is not None else estimate_upcoming_curve(None, 0.0)

    p0 = ps_list[0] if len(ps_list) > 0 else None
    battery_v = round(float(p0.voltage) / 1000.0, 2) if p0 is not None else 0.0
    safety_model = str(p0.safetyModel) if p0 is not None else "unknown"
    safety_param = int(p0.safetyParam) if p0 is not None else 0
    controls_allowed = bool(p0.controlsAllowed) if p0 is not None else False

    return {
      "timestamp": time.monotonic(),
      "vehicle": {
        "speed_mps": round(v_ego, 2),
        "speed_mph": round(v_ego * CV.MS_TO_MPH, 1),
        "speed_kph": round(v_ego * CV.MS_TO_KPH, 1),
        "accel_mps2": round(float(cs.aEgo), 2) if cs is not None else 0.0,
        "lat_g": round(lat_g, 2),
        "steering_angle_deg": round(steer_deg, 1),
        "desired_angle_deg": round(float(cc.actuators.steeringAngleDeg), 1) if cc is not None else 0.0,
        "steering_torque_nm": round(steer_tq, 2),
        "steering_pressed": bool(cs.steeringPressed) if cs is not None else False,
        "gas_pressed": bool(cs.gasPressed) if cs is not None else False,
        "brake_pressed": bool(cs.brakePressed) if cs is not None else False,
        "parking_brake": bool(cs.parkingBrake) if cs is not None else False,
        "standstill": bool(cs.standstill) if cs is not None else True,
        "gear": str(cs.gearShifter) if cs is not None else "park",
        "left_blinker": bool(cs.leftBlinker) if cs is not None else False,
        "right_blinker": bool(cs.rightBlinker) if cs is not None else False,
        "left_blindspot": bool(cs.leftBlindspot) if cs is not None else False,
        "right_blindspot": bool(cs.rightBlindspot) if cs is not None else False,
        "door_open": bool(cs.doorOpen) if cs is not None else False,
        "seatbelt_unlatched": bool(cs.seatbeltUnlatched) if cs is not None else False,
        "cruise_available": bool(cs.cruiseState.available) if cs is not None else False,
        "cruise_enabled": bool(cs.cruiseState.enabled) if cs is not None else False,
        "cruise_set_mph": round(float(cs.cruiseState.speed) * CV.MS_TO_MPH, 0) if cs is not None else 0.0,
        "can_valid": bool(cs.canValid) if cs is not None else False,
        "steer_fault_temp": bool(cs.steerFaultTemporary) if cs is not None else False,
        "steer_fault_perm": bool(cs.steerFaultPermanent) if cs is not None else False,
        "roll_deg": round(roll_deg, 1),
        "pitch_deg": round(pitch_deg, 1),
      },
      "ford_can": self._extended_can,
      "openpilot": {
        "enabled": bool(ss.enabled) if ss is not None else False,
        "active": bool(ss.active) if ss is not None else False,
        "lat_active": bool(cc.latActive) if cc is not None else False,
        "state": str(ss.state) if ss is not None else "disabled",
        "alert_text1": str(ss.alertText1) if ss is not None else "",
        "alert_text2": str(ss.alertText2) if ss is not None else "",
        "controls_allowed": controls_allowed,
        "safety_model": safety_model,
        "safety_param": safety_param,
        "battery_v": battery_v,
        "cpu_temp_c": round(max(list(ds.cpuTempC) or [0.0]), 1) if ds is not None else 0.0,
        "started": bool(ds.started) if ds is not None else False,
      },
      "curve": {
        "max_curvature": curve_est.max_curvature,
        "peak_distance_m": curve_est.peak_distance_m,
        "recommended_speed_mps": curve_est.recommended_speed_mps,
        "recommended_speed_mph": round(curve_est.recommended_speed_mps * CV.MS_TO_MPH, 0),
        "warning_active": curve_est.warning_active,
      },
      "gps": self._last_gps,
      "nav": nav.__dict__,
      "config": cfg.__dict__,
    }

  def get_diagnostics_summary(self) -> dict:
    snap = self.update_and_snapshot()
    ecus = []
    fingerprint = "FORD_BRONCO_SPORT_MK1"
    flags = 0
    dashcam_only = False

    if self.params is not None:
      try:
        from openpilot.cereal import messaging
        from opendbc.car.structs import car

        cp_bytes = self.params.get("CarParams") or self.params.get("CarParamsPersistent")
        if cp_bytes:
          cp = messaging.log_from_bytes(cp_bytes, car.CarParams)
          fingerprint = str(cp.carFingerprint)
          flags = int(cp.flags)
          dashcam_only = bool(cp.dashcamOnly)
          for fw in cp.carFw:
            ecus.append(
              {
                "ecu": str(fw.ecu),
                "address": f"0x{int(fw.address):03X}",
                "sub_address": int(fw.subAddress),
                "fw_version": fw.fwVersion.decode("utf-8", errors="replace"),
                "bus": int(fw.bus),
              }
            )
      except Exception:
        pass

    v = snap["vehicle"]
    f = snap["ford_can"]
    op = snap["openpilot"]
    can_ok = bool(v["can_valid"] or not op["started"])
    epas_ok = not (v["steer_fault_temp"] or v["steer_fault_perm"])
    fault_checks = [
      {
        "name": "CAN Bus Validity",
        "ok": can_ok,
        "detail": "Bus 0 & Bus 2 healthy" if v["can_valid"] else "Park / Offroad or waiting for CAN",
      },
      {
        "name": "PSCM / EPAS Steering Status",
        "ok": epas_ok,
        "detail": "No EPAS fault" if epas_ok else "EPAS fault reported",
      },
      {
        "name": "Steering Pinion Calibration (0x7E)",
        "ok": f["pinion_qf"] == 3,
        "detail": f"Quality factor Qf={f['pinion_qf']} (3=Calibrated)",
      },
      {
        "name": "Panda Safety Model",
        "ok": op["safety_param"] == 12 or not op["started"],
        "detail": f"Model={op['safety_model']}, Param={op['safety_param']}",
      },
      {
        "name": "Body / Service Indicator",
        "ok": not f["service_required"],
        "detail": "Normal" if not f["service_required"] else "BodySrvcRqd active",
      },
    ]

    return {
      "fingerprint": fingerprint,
      "flags": flags,
      "dashcam_only": dashcam_only,
      "ecus": ecus,
      "fault_checks": fault_checks,
      "battery_v": op["battery_v"],
      "eps_voltage_v": f["eps_voltage_v"],
    }
