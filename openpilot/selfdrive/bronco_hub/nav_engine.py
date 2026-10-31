import json
import math
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field

from openpilot.common.constants import CV
from openpilot.selfdrive.controls.lib.bronco_config import BroncoNavState, save_nav_state

USER_AGENT = "OpenpilotBroncoHub/0.11.2"

_COORD_PAIR_RE = re.compile(r"^\s*(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*$")
_AT_COORD_RE = re.compile(r"@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)")
_BANG_COORD_RE = re.compile(r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)")
_EMBED_COORD_RE = re.compile(r"(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  r = 6371000.0
  p1 = math.radians(lat1)
  p2 = math.radians(lat2)
  dp = math.radians(lat2 - lat1)
  dl = math.radians(lon2 - lon1)
  a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
  return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def parse_osm_maxspeed_mps(raw: str | None) -> float:
  """Parse an OpenStreetMap maxspeed tag (e.g. '50', '45 mph', '80 km/h') into m/s."""
  if not raw:
    return 0.0
  text = str(raw).strip().lower()
  m = re.search(r"(\d+(?:\.\d+)?)", text)
  if not m:
    return 0.0
  val = float(m.group(1))
  if val <= 0 or val > 200:
    return 0.0
  if "mph" in text or "mi" in text:
    return round(val * CV.MPH_TO_MS, 2)
  return round(val * CV.KPH_TO_MS, 2)


def _valid_lat_lon(lat: float, lon: float) -> bool:
  return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and not (abs(lat) < 1e-6 and abs(lon) < 1e-6)


def parse_coordinates_from_text(raw_text: str) -> tuple[float, float, str] | None:
  """Extract (lat, lon, label) from Google Maps, Apple Maps, Waze share links, or coordinate strings without network calls."""
  if not raw_text:
    return None
  text = raw_text.strip()

  m = _COORD_PAIR_RE.match(text)
  if m:
    lat, lon = float(m.group(1)), float(m.group(2))
    if _valid_lat_lon(lat, lon):
      return lat, lon, f"{lat:.5f}, {lon:.5f}"

  # Parse URL query parameters if present
  if "http://" in text or "https://" in text or "?" in text:
    url_match = re.search(r"(https?://\S+)", text)
    url_str = url_match.group(1) if url_match else text
    parsed = urllib.parse.urlparse(url_str)
    qs = urllib.parse.parse_qs(parsed.query)
    label = ""
    for name_key in ("q", "query", "daddr", "destination", "address"):
      if name_key in qs and qs[name_key]:
        candidate = qs[name_key][0].strip()
        if not _COORD_PAIR_RE.match(candidate):
          label = candidate

    for coord_key in ("ll", "sll", "q", "query", "daddr", "destination", "center"):
      if coord_key in qs and qs[coord_key]:
        cm = _EMBED_COORD_RE.search(qs[coord_key][0])
        if cm:
          lat, lon = float(cm.group(1)), float(cm.group(2))
          if _valid_lat_lon(lat, lon):
            return lat, lon, label or f"{lat:.5f}, {lon:.5f}"

    # Check Google Maps /place/<Name>/@lat,lon or !3dlat!4dlon
    place_match = re.search(r"/place/([^/@]+)", parsed.path)
    if place_match and not label:
      label = urllib.parse.unquote_plus(place_match.group(1))

    bm = _BANG_COORD_RE.search(url_str)
    if bm:
      lat, lon = float(bm.group(1)), float(bm.group(2))
      if _valid_lat_lon(lat, lon):
        return lat, lon, label or f"{lat:.5f}, {lon:.5f}"

    am = _AT_COORD_RE.search(url_str)
    if am:
      lat, lon = float(am.group(1)), float(am.group(2))
      if _valid_lat_lon(lat, lon):
        return lat, lon, label or f"{lat:.5f}, {lon:.5f}"

  em = _EMBED_COORD_RE.search(text)
  if em:
    lat, lon = float(em.group(1)), float(em.group(2))
    if _valid_lat_lon(lat, lon):
      return lat, lon, f"{lat:.5f}, {lon:.5f}"

  return None


def resolve_destination_input(raw_text: str, timeout_s: float = 4.0) -> tuple[float, float, str]:
  """Resolve coordinates, Google/Apple/Waze URLs (including short redirects), or address search via Nominatim."""
  direct = parse_coordinates_from_text(raw_text)
  if direct is not None:
    return direct

  text = (raw_text or "").strip()
  if not text:
    raise ValueError("Empty destination input")

  # If it is a short URL (e.g. maps.app.goo.gl), follow redirect to inspect target URL
  url_match = re.search(r"(https?://\S+)", text)
  if url_match:
    short_url = url_match.group(1)
    req = urllib.request.Request(short_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
      final_url = resp.geturl()
    resolved = parse_coordinates_from_text(final_url)
    if resolved is not None:
      return resolved
    # Check if redirected URL has ?q=address
    parsed = urllib.parse.urlparse(final_url)
    qs = urllib.parse.parse_qs(parsed.query)
    for k in ("q", "query", "daddr", "destination", "address"):
      if k in qs and qs[k]:
        text = qs[k][0]
        break

  # Geocode text address via OpenStreetMap Nominatim
  encoded = urllib.parse.urlencode({"q": text, "format": "jsonv2", "limit": 1})
  nom_url = f"https://nominatim.openstreetmap.org/search?{encoded}"
  req = urllib.request.Request(nom_url, headers={"User-Agent": USER_AGENT})
  with urllib.request.urlopen(req, timeout=timeout_s) as resp:
    results = json.loads(resp.read().decode("utf-8"))
  if not results:
    raise ValueError(f"Could not find location for: {text}")
  top = results[0]
  lat = float(top["lat"])
  lon = float(top["lon"])
  name = str(top.get("name") or top.get("display_name") or text).split(",")[0].strip()
  return lat, lon, name


def recommended_speed_for_maneuver(maneuver_type: str, modifier: str) -> float:
  mtype = (maneuver_type or "").lower()
  mod = (modifier or "").lower()
  if mtype in ("arrive", "depart"):
    return 5.0
  if "uturn" in mod or "sharp" in mod:
    return 6.5  # ~15 mph
  if mtype in ("roundabout", "rotary"):
    return 8.0  # ~18 mph
  if mtype in ("off ramp", "on ramp", "ramp", "fork", "merge"):
    return 15.6  # ~35 mph
  if "slight" in mod or "straight" in mod:
    return 17.8  # ~40 mph
  if "left" in mod or "right" in mod or mtype == "turn":
    return 9.8  # ~22 mph
  return 20.0


def format_maneuver_instruction(maneuver_type: str, modifier: str, street_name: str) -> str:
  mtype = (maneuver_type or "turn").replace("_", " ").strip()
  mod = (modifier or "").replace("_", " ").strip()
  target = f" onto {street_name}" if street_name else ""
  if mtype == "arrive":
    return f"Arrive at destination{target}"
  if mtype == "depart":
    return f"Head {mod or 'forward'}{target}"
  if mtype in ("off ramp", "on ramp", "ramp"):
    return f"Take ramp {mod}{target}".strip()
  if mtype == "fork":
    return f"Keep {mod} at fork{target}".strip()
  if mtype == "merge":
    return f"Merge {mod}{target}".strip()
  if mtype in ("roundabout", "rotary"):
    return f"Take roundabout{target}"
  if mod:
    return f"Turn {mod}{target}"
  return f"{mtype.capitalize()}{target}"


@dataclass
class RouteStep:
  lat: float
  lon: float
  maneuver_type: str
  modifier: str
  street_name: str
  distance_m: float
  duration_s: float
  instruction: str
  recommended_speed_mps: float


@dataclass
class ActiveRoute:
  destination_name: str
  dest_lat: float
  dest_lon: float
  total_distance_m: float
  total_duration_s: float
  geometry: list[list[float]] = field(default_factory=list)  # [[lat, lon], ...]
  steps: list[RouteStep] = field(default_factory=list)
  current_step_idx: int = 0


class NavigationEngine:
  """Manages turn-by-turn routing, live GPS step progression, and OSM speed limits."""

  def __init__(self):
    self.route: ActiveRoute | None = None
    self.speed_limit_mps: float = 0.0
    self.speed_limit_updated_at: float = 0.0
    self._last_osm_query_mono: float = 0.0
    self._last_osm_lat: float = 0.0
    self._last_osm_lon: float = 0.0

  def clear_route(self) -> BroncoNavState:
    self.route = None
    state = BroncoNavState(
      active=False,
      speed_limit_mps=self.speed_limit_mps,
      speed_limit_updated_at=self.speed_limit_updated_at,
      updated_at=time.monotonic(),
    )
    save_nav_state(state)
    return state

  def build_route_from_osrm_json(
    self,
    osrm_data: dict,
    dest_lat: float,
    dest_lon: float,
    destination_name: str,
  ) -> ActiveRoute:
    routes = osrm_data.get("routes") or []
    if not routes:
      raise ValueError("No route returned by routing engine")
    r0 = routes[0]
    coords_lon_lat = (r0.get("geometry") or {}).get("coordinates") or []
    geometry = [[float(c[1]), float(c[0])] for c in coords_lon_lat if len(c) >= 2]

    steps: list[RouteStep] = []
    for leg in r0.get("legs") or []:
      for st in leg.get("steps") or []:
        man = st.get("maneuver") or {}
        loc = man.get("location") or [dest_lon, dest_lat]
        mtype = str(man.get("type") or "turn")
        modifier = str(man.get("modifier") or "")
        street = str(st.get("name") or st.get("ref") or "").strip()
        if mtype == "depart" and len(steps) == 0:
          continue
        steps.append(
          RouteStep(
            lat=float(loc[1]),
            lon=float(loc[0]),
            maneuver_type=mtype,
            modifier=modifier,
            street_name=street,
            distance_m=float(st.get("distance") or 0.0),
            duration_s=float(st.get("duration") or 0.0),
            instruction=format_maneuver_instruction(mtype, modifier, street),
            recommended_speed_mps=recommended_speed_for_maneuver(mtype, modifier),
          )
        )

    if not steps:
      steps.append(
        RouteStep(
          lat=dest_lat,
          lon=dest_lon,
          maneuver_type="arrive",
          modifier="",
          street_name=destination_name,
          distance_m=float(r0.get("distance") or 0.0),
          duration_s=float(r0.get("duration") or 0.0),
          instruction=f"Arrive at {destination_name}",
          recommended_speed_mps=5.0,
        )
      )

    self.route = ActiveRoute(
      destination_name=destination_name,
      dest_lat=dest_lat,
      dest_lon=dest_lon,
      total_distance_m=float(r0.get("distance") or 0.0),
      total_duration_s=float(r0.get("duration") or 0.0),
      geometry=geometry,
      steps=steps,
      current_step_idx=0,
    )
    return self.route

  def start_route(
    self,
    start_lat: float,
    start_lon: float,
    dest_lat: float,
    dest_lon: float,
    destination_name: str = "Destination",
    timeout_s: float = 5.0,
  ) -> ActiveRoute:
    coords_part = f"{start_lon:.6f},{start_lat:.6f};{dest_lon:.6f},{dest_lat:.6f}"
    url = f"https://router.project-osrm.org/route/v1/driving/{coords_part}?overview=full&geometries=geojson&steps=true"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
      payload = json.loads(resp.read().decode("utf-8"))
    route = self.build_route_from_osrm_json(payload, dest_lat, dest_lon, destination_name)
    self.update_position(start_lat, start_lon)
    return route

  def update_position(self, lat: float, lon: float, v_ego_mps: float = 0.0) -> BroncoNavState:
    if self.route is None or not self.route.steps:
      state = BroncoNavState(
        active=False,
        speed_limit_mps=self.speed_limit_mps,
        speed_limit_updated_at=self.speed_limit_updated_at,
        updated_at=time.monotonic(),
      )
      save_nav_state(state)
      return state

    rt = self.route
    while rt.current_step_idx < len(rt.steps) - 1:
      cur_step = rt.steps[rt.current_step_idx]
      dist_cur = haversine_m(lat, lon, cur_step.lat, cur_step.lon)
      if dist_cur <= 28.0:
        rt.current_step_idx += 1
      else:
        break

    step = rt.steps[rt.current_step_idx]
    dist_to_next = haversine_m(lat, lon, step.lat, step.lon)

    # Arrive check on final step
    if rt.current_step_idx == len(rt.steps) - 1 and dist_to_next <= 25.0:
      return self.clear_route()

    rem_dist = dist_to_next + sum(s.distance_m for s in rt.steps[rt.current_step_idx + 1 :])
    rem_dur = (dist_to_next / max(v_ego_mps, 8.0)) + sum(s.duration_s for s in rt.steps[rt.current_step_idx + 1 :])

    # Compute navigation lane-change desire when approaching ramps/turns
    nav_desire = "none"
    mod = (step.modifier or "").lower()
    mtype = (step.maneuver_type or "").lower()
    if 40.0 <= dist_to_next <= 260.0 and mtype in ("off ramp", "on ramp", "ramp", "fork", "turn"):
      if "left" in mod:
        nav_desire = "laneChangeLeft"
      elif "right" in mod:
        nav_desire = "laneChangeRight"

    state = BroncoNavState(
      active=True,
      destination_name=rt.destination_name,
      dest_lat=rt.dest_lat,
      dest_lon=rt.dest_lon,
      distance_remaining_m=round(rem_dist, 1),
      duration_remaining_s=round(rem_dur, 1),
      next_maneuver_type=step.maneuver_type,
      next_maneuver_modifier=step.modifier,
      next_maneuver_instruction=step.instruction,
      next_street_name=step.street_name,
      distance_to_next_m=round(dist_to_next, 1),
      recommended_turn_speed_mps=round(step.recommended_speed_mps, 2),
      speed_limit_mps=round(self.speed_limit_mps, 2),
      speed_limit_updated_at=self.speed_limit_updated_at,
      nav_desire=nav_desire,
      updated_at=time.monotonic(),
    )
    save_nav_state(state)
    return state

  def maybe_refresh_osm_speed_limit(self, lat: float, lon: float, timeout_s: float = 2.5) -> float:
    if not _valid_lat_lon(lat, lon):
      self.speed_limit_mps = 0.0
      self.speed_limit_updated_at = 0.0
      return self.speed_limit_mps
    now = time.monotonic()
    if (now - self._last_osm_query_mono) < 20.0 and haversine_m(lat, lon, self._last_osm_lat, self._last_osm_lon) < 180.0:
      return self.speed_limit_mps

    # A previous road's limit must not survive a failed or empty lookup.
    self.speed_limit_mps = 0.0
    self.speed_limit_updated_at = 0.0
    self._last_osm_query_mono = now
    self._last_osm_lat = lat
    self._last_osm_lon = lon

    query = f'[out:json][timeout:2];way(around:35,{lat:.5f},{lon:.5f})["highway"]["maxspeed"];out tags 1;'
    url = "https://overpass-api.de/api/interpreter?" + urllib.parse.urlencode({"data": query})
    try:
      req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
      with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
      elements = data.get("elements") or []
      if elements:
        maxspeed_raw = (elements[0].get("tags") or {}).get("maxspeed")
        parsed = parse_osm_maxspeed_mps(maxspeed_raw)
        if parsed > 0:
          self.speed_limit_mps = parsed
          self.speed_limit_updated_at = time.monotonic()
    except Exception:
      pass
    return self.speed_limit_mps

  def to_dict(self) -> dict:
    if self.route is None:
      return {"active": False, "speed_limit_mps": self.speed_limit_mps}
    return {
      "active": True,
      "speed_limit_mps": self.speed_limit_mps,
      "route": asdict(self.route),
    }
