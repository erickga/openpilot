import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CurveSpeedEstimate:
  max_curvature: float = 0.0
  peak_distance_m: float = 0.0
  recommended_speed_mps: float = 99.0
  warning_active: bool = False


def estimate_upcoming_curve(model_v2, v_ego_mps: float, max_lat_accel: float = 2.2) -> CurveSpeedEstimate:
  """Estimate peak upcoming road curvature and comfortable entry speed from modelV2."""
  if model_v2 is None or not hasattr(model_v2, "position"):
    return CurveSpeedEstimate()

  xs = list(getattr(model_v2.position, "x", []) or [])
  ys = list(getattr(model_v2.position, "y", []) or [])
  n = min(len(xs), len(ys))
  if n < 5:
    return CurveSpeedEstimate()

  min_dist = 10.0
  max_dist = min(130.0, max(35.0, float(v_ego_mps) * 4.5))

  peak_kappa = 0.0
  peak_dist = 0.0

  # 3-point Menger curvature across separated points along the predicted trajectory
  step = 2
  for i in range(step, n - step):
    x1, y1 = float(xs[i - step]), float(ys[i - step])
    x2, y2 = float(xs[i]), float(ys[i])
    x3, y3 = float(xs[i + step]), float(ys[i + step])
    if x2 < min_dist or x2 > max_dist:
      continue

    a = math.hypot(x2 - x1, y2 - y1)
    b = math.hypot(x3 - x2, y3 - y2)
    c = math.hypot(x3 - x1, y3 - y1)
    if a < 1.0 or b < 1.0 or c < 2.0:
      continue

    twice_area = abs((x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1))
    denom = a * b * c
    if denom <= 1e-6:
      continue
    kappa = (2.0 * twice_area) / denom
    if kappa > peak_kappa:
      peak_kappa = kappa
      peak_dist = x2

  # Also check orientationRate.z / velocity.x when available
  if hasattr(model_v2, "orientationRate") and hasattr(model_v2, "velocity"):
    yaw_rates = list(getattr(model_v2.orientationRate, "z", []) or [])
    vels = list(getattr(model_v2.velocity, "x", []) or [])
    m = min(n, len(yaw_rates), len(vels))
    for i in range(m):
      dist = float(xs[i])
      if dist < min_dist or dist > max_dist:
        continue
      vx = max(float(vels[i]), 3.0)
      kappa = abs(float(yaw_rates[i])) / vx
      if kappa > peak_kappa:
        peak_kappa = kappa
        peak_dist = dist

  if peak_kappa <= 1e-4:
    return CurveSpeedEstimate()

  lat_limit = max(1.2, float(max_lat_accel))
  rec_mps = math.sqrt(lat_limit / peak_kappa)
  warning = bool(v_ego_mps > 8.5 and peak_kappa >= 0.0035 and (v_ego_mps - rec_mps) >= 1.3)
  return CurveSpeedEstimate(
    max_curvature=round(peak_kappa, 5),
    peak_distance_m=round(peak_dist, 1),
    recommended_speed_mps=round(rec_mps, 2),
    warning_active=warning,
  )


def is_road_edge_blocking_lane_change(
  model_v2,
  direction_left: bool,
  max_edge_std: float = 0.40,
  min_edge_distance_m: float = 2.8,
) -> bool:
  """Return True if a high-confidence road edge is too close on the target lane-change side."""
  if model_v2 is None or not hasattr(model_v2, "roadEdges") or not hasattr(model_v2, "roadEdgeStds"):
    return False

  edges = getattr(model_v2, "roadEdges", None)
  stds = getattr(model_v2, "roadEdgeStds", None)
  if not edges or not stds or len(edges) < 2 or len(stds) < 2:
    return False

  idx = 0 if direction_left else 1
  edge_std = float(stds[idx])
  if edge_std > max_edge_std:
    return False

  ys = list(getattr(edges[idx], "y", []) or [])
  if not ys:
    return False

  # Sample near-field lateral distance (first ~5 points)
  sample = ys[: min(5, len(ys))]
  near_dist = min(abs(float(y)) for y in sample)
  return near_dist < min_edge_distance_m
