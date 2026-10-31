from http.server import ThreadingHTTPServer
import json
import math
import sys
import threading
from types import SimpleNamespace
import urllib.request
import urllib.error
import http.cookiejar
import pytest
import time
from unittest.mock import Mock
from pathlib import Path

sys.modules.setdefault("setproctitle", SimpleNamespace(getproctitle=lambda: "python", setproctitle=lambda _: None))

from openpilot.cereal import log
from openpilot.common.constants import CV
from openpilot.selfdrive.bronco_hub.curve_speed import (
  estimate_upcoming_curve,
  is_road_edge_blocking_lane_change,
)
from openpilot.selfdrive.bronco_hub.nav_engine import (
  NavigationEngine,
  parse_coordinates_from_text,
  parse_osm_maxspeed_mps,
)
from openpilot.selfdrive.bronco_hub.server import BroncoHubRuntime, make_handler
from openpilot.selfdrive.bronco_hub.telemetry import (
  BroncoTelemetryCollector,
  list_recorded_routes,
  resolve_segment_file,
)
from openpilot.selfdrive.controls.lib.bronco_config import (
  BroncoHubConfig,
  BroncoNavState,
  CachedBroncoStore,
  load_bronco_config,
  save_bronco_config,
  save_nav_state,
)
from openpilot.selfdrive.controls.lib.desire_helper import (
  DesireHelper,
  LaneChangeDirection,
  LaneChangeState,
  should_pause_lateral_on_blinker,
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
  monkeypatch.setenv("BRONCO_HUB_KEY_PATH", str(tmp_path / "key"))
  monkeypatch.setenv("BRONCO_HUB_CONFIG_PATH", str(tmp_path / "config.json"))
  monkeypatch.setenv("BRONCO_HUB_NAV_STATE_PATH", str(tmp_path / "nav.json"))


def _make_carstate(
  v_ego_mph: float = 45.0,
  left_blinker: bool = False,
  right_blinker: bool = False,
  steering_pressed: bool = False,
  steering_torque: float = 0.0,
  left_blindspot: bool = False,
  right_blindspot: bool = False,
):
  return SimpleNamespace(
    vEgo=v_ego_mph * CV.MPH_TO_MS,
    leftBlinker=left_blinker,
    rightBlinker=right_blinker,
    steeringPressed=steering_pressed,
    steeringTorque=steering_torque,
    leftBlindspot=left_blindspot,
    rightBlindspot=right_blindspot,
  )


def test_config_roundtrip_and_clamping(tmp_path, monkeypatch):
  cfg_path = tmp_path / "bronco_cfg.json"
  nav_path = tmp_path / "bronco_nav.json"
  monkeypatch.setenv("BRONCO_HUB_CONFIG_PATH", str(cfg_path))
  monkeypatch.setenv("BRONCO_HUB_NAV_STATE_PATH", str(nav_path))

  saved = save_bronco_config(
    {
      "blinker_mode": "auto",
      "auto_lane_change_delay_sec": 0.05,  # clamped to 0.2
      "auto_lane_change_min_mph": 10.0,  # clamped to 15.0
      "pause_lateral_below_mph": 30.0,
      "blindspot_path_glow": False,
    }
  )
  assert saved.blinker_mode == "auto"
  assert saved.auto_lane_change_delay_sec == 0.2
  assert saved.auto_lane_change_min_mph == 15.0
  assert saved.pause_lateral_below_mph == 30.0
  assert saved.blindspot_path_glow is False

  store = CachedBroncoStore(poll_interval_s=0.0, config_path=cfg_path, nav_path=nav_path)
  cfg, nav = store.refresh()
  assert cfg.blinker_mode == "auto"
  assert nav.active is False

  save_nav_state({"active": True, "destination_name": "Guadalajara", "distance_to_next_m": 150.0})
  _, nav2 = store.refresh()
  assert nav2.active is True
  assert nav2.destination_name == "Guadalajara"
  assert nav2.is_fresh()


def test_desire_helper_auto_lane_change_and_bsm_reset(tmp_path, monkeypatch):
  monkeypatch.setenv("BRONCO_HUB_CONFIG_PATH", str(tmp_path / "cfg.json"))
  dh = DesireHelper()
  cfg = BroncoHubConfig(blinker_mode="auto", auto_lane_change_delay_sec=0.25, auto_lane_change_min_mph=25.0)

  # 1. Start with blinker off
  cs_off = _make_carstate(v_ego_mph=50.0)
  dh.update(cs_off, True, 0.0, config=cfg)
  assert dh.lane_change_state == LaneChangeState.off

  # 2. Turn left blinker on -> preLaneChange
  cs_left = _make_carstate(v_ego_mph=50.0, left_blinker=True)
  dh.update(cs_left, True, 0.0, config=cfg)
  assert dh.lane_change_state == LaneChangeState.preLaneChange
  assert dh.lane_change_direction == LaneChangeDirection.left

  # 3. Accumulate 2 frames (0.10s), then blindspot triggers -> timer resets to 0
  dh.update(cs_left, True, 0.0, config=cfg)
  dh.update(cs_left, True, 0.0, config=cfg)
  assert dh.auto_lc_timer > 0.0

  cs_bsm = _make_carstate(v_ego_mph=50.0, left_blinker=True, left_blindspot=True)
  dh.update(cs_bsm, True, 0.0, config=cfg)
  assert dh.lane_change_state == LaneChangeState.preLaneChange
  assert dh.auto_lc_timer == 0.0

  # 4. Blindspot clears -> after 5 frames (0.25s) auto lane change starts without steering nudge
  for _ in range(5):
    dh.update(cs_left, True, 0.5, config=cfg)
  assert dh.lane_change_state == LaneChangeState.laneChangeStarting
  assert dh.desire == log.Desire.laneChangeLeft


def test_desire_helper_road_edge_blocks_auto_lane_change():
  dh = DesireHelper()
  cfg = BroncoHubConfig(blinker_mode="auto", auto_lane_change_delay_sec=0.2, road_edge_lane_change_block=True)

  # Fake modelV2 with a close left road edge (y = -1.8m, std = 0.15)
  fake_model = SimpleNamespace(
    roadEdges=[SimpleNamespace(y=[-1.8, -1.8, -1.8]), SimpleNamespace(y=[4.5, 4.5, 4.5])],
    roadEdgeStds=[0.15, 0.15],
  )
  assert is_road_edge_blocking_lane_change(fake_model, direction_left=True) is True
  assert is_road_edge_blocking_lane_change(fake_model, direction_left=False) is False

  cs_left = _make_carstate(v_ego_mph=50.0, left_blinker=True)
  dh.update(cs_left, True, 0.0, model_v2=fake_model, config=cfg)
  for _ in range(10):
    dh.update(cs_left, True, 0.5, model_v2=fake_model, config=cfg)
  assert dh.lane_change_state == LaneChangeState.preLaneChange


def test_pause_lateral_on_blinker():
  cfg_pause = BroncoHubConfig(blinker_mode="pause")
  cs_blink = _make_carstate(v_ego_mph=55.0, right_blinker=True)
  assert should_pause_lateral_on_blinker(cs_blink, cfg_pause) is True

  dh = DesireHelper()
  dh.update(cs_blink, True, 0.0, config=cfg_pause)
  assert dh.lane_change_state == LaneChangeState.off
  assert dh.desire == log.Desire.none

  cfg_city = BroncoHubConfig(blinker_mode="auto", pause_lateral_below_mph=30.0)
  assert should_pause_lateral_on_blinker(_make_carstate(v_ego_mph=22.0, left_blinker=True), cfg_city) is True
  assert should_pause_lateral_on_blinker(_make_carstate(v_ego_mph=45.0, left_blinker=True), cfg_city) is False


def test_curve_speed_estimator():
  # Straight path -> no warning
  xs = [float(i * 4) for i in range(30)]
  ys_straight = [0.0 for _ in xs]
  model_straight = SimpleNamespace(position=SimpleNamespace(x=xs, y=ys_straight))
  est_straight = estimate_upcoming_curve(model_straight, v_ego_mps=25.0)
  assert est_straight.warning_active is False

  # Circular arc radius R = 70m -> kappa ~ 1/70 = 0.01428 -> v_rec = sqrt(2.2 * 70) ~ 12.4 m/s
  radius = 70.0
  ys_curve = [radius - math.sqrt(max(0.0, radius**2 - x**2)) for x in xs if x < 65.0]
  xs_curve = xs[: len(ys_curve)]
  model_curve = SimpleNamespace(position=SimpleNamespace(x=xs_curve, y=ys_curve))
  est_curve = estimate_upcoming_curve(model_curve, v_ego_mps=22.0, max_lat_accel=2.2)
  assert est_curve.max_curvature > 0.01
  assert 10.0 <= est_curve.recommended_speed_mps <= 15.0
  assert est_curve.warning_active is True


def test_parse_links_and_navigation_engine(tmp_path, monkeypatch):
  monkeypatch.setenv("BRONCO_HUB_NAV_STATE_PATH", str(tmp_path / "nav.json"))

  # Google Maps @lat,lon link
  g = parse_coordinates_from_text("https://www.google.com/maps/place/Minerva/@20.6744,-103.3874,17z")
  assert g == (20.6744, -103.3874, "Minerva")

  # Apple Maps link
  a = parse_coordinates_from_text("https://maps.apple.com/?ll=20.6597,-103.3496&q=Centro+Historico")
  assert a == (20.6597, -103.3496, "Centro Historico")

  # Waze link
  w = parse_coordinates_from_text("https://waze.com/ul?ll=20.7011,-103.4122&navigate=yes")
  assert w == (20.7011, -103.4122, "20.70110, -103.41220")

  assert parse_osm_maxspeed_mps("60") == round(60 * CV.KPH_TO_MS, 2)
  assert parse_osm_maxspeed_mps("45 mph") == round(45 * CV.MPH_TO_MS, 2)

  # Route step progression
  eng = NavigationEngine()
  fake_osrm = {
    "routes": [
      {
        "distance": 1200.0,
        "duration": 95.0,
        "geometry": {"coordinates": [[-103.3874, 20.6744], [-103.3850, 20.6744], [-103.3850, 20.6780]]},
        "legs": [
          {
            "steps": [
              {
                "distance": 250.0,
                "duration": 20.0,
                "name": "Av. Vallarta",
                "maneuver": {"type": "depart", "modifier": "east", "location": [-103.3874, 20.6744]},
              },
              {
                "distance": 450.0,
                "duration": 35.0,
                "name": "Av. Lopez Mateos",
                "maneuver": {"type": "off ramp", "modifier": "right", "location": [-103.3860, 20.6744]},
              },
              {
                "distance": 0.0,
                "duration": 0.0,
                "name": "Destination",
                "maneuver": {"type": "arrive", "modifier": "", "location": [-103.3850, 20.6780]},
              },
            ]
          }
        ],
      }
    ]
  }
  eng.build_route_from_osrm_json(fake_osrm, 20.6780, -103.3850, "Midtown")
  st1 = eng.update_position(20.6744, -103.3874, v_ego_mps=18.0)
  assert st1.active is True
  assert st1.next_street_name == "Av. Lopez Mateos"
  assert st1.nav_desire == "laneChangeRight"

  # Move to final arrival point -> route completes and clears
  st_done = eng.update_position(20.6744, -103.3860, v_ego_mps=10.0)
  assert st_done.active is True
  st_arrived = eng.update_position(20.6780, -103.3850, v_ego_mps=2.0)
  assert st_arrived.active is False


def test_realdata_routes_and_http_server(tmp_path, monkeypatch):
  monkeypatch.setenv("BRONCO_HUB_CONFIG_PATH", str(tmp_path / "cfg.json"))
  monkeypatch.setenv("BRONCO_HUB_NAV_STATE_PATH", str(tmp_path / "nav.json"))

  realdata = tmp_path / "realdata"
  seg_dir = realdata / "00000707--adde5d06d4--0"
  seg_dir.mkdir(parents=True)
  (seg_dir / "fcamera.hevc").write_bytes(b"FAKE_HEVC_BYTES")

  routes = list_recorded_routes(realdata)
  assert len(routes) == 1
  assert routes[0]["route_id"] == "00000707--adde5d06d4"
  assert resolve_segment_file(realdata, "00000707--adde5d06d4", 0, "fcamera.hevc") is not None
  assert resolve_segment_file(realdata, "../etc", 0, "passwd") is None

  runtime = BroncoHubRuntime(enable_ipc=False, realdata_dir=realdata)
  server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(runtime))
  port = server.server_address[1]
  t = threading.Thread(target=server.serve_forever, daemon=True)
  t.start()
  opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
  try:
    with pytest.raises(urllib.error.HTTPError) as denied:
      opener.open(f"http://127.0.0.1:{port}/api/telemetry", timeout=3)
    assert denied.value.code == 401
    pair = urllib.request.Request(
      f"http://127.0.0.1:{port}/api/pair", data=json.dumps({"key": runtime.pairing_key}).encode(),
      headers={"Content-Type": "application/json"}, method="POST",
    )
    with opener.open(pair, timeout=3) as resp:
      assert "HttpOnly" in resp.headers["Set-Cookie"]
      assert "SameSite=Strict" in resp.headers["Set-Cookie"]
    with opener.open(f"http://127.0.0.1:{port}/api/telemetry", timeout=3) as resp:
      telem = json.loads(resp.read().decode("utf-8"))
      assert "vehicle" in telem and "ford_can" in telem

    req = urllib.request.Request(
      f"http://127.0.0.1:{port}/api/config",
      data=json.dumps({"blinker_mode": "auto", "auto_lane_change_delay_sec": 0.8}).encode("utf-8"),
      headers={"Content-Type": "application/json"},
      method="POST",
    )
    # No IPC / unknown device state must not authorize changes.
    with pytest.raises(urllib.error.HTTPError) as denied:
      opener.open(req, timeout=3)
    assert denied.value.code == 409
    assert load_bronco_config().blinker_mode == "nudge"
    monkeypatch.setattr(runtime.collector, "config_changes_allowed", lambda: True)
    with opener.open(req, timeout=3) as resp:
      saved = json.loads(resp.read().decode("utf-8"))
      assert saved["blinker_mode"] == "auto"
      assert saved["auto_lane_change_delay_sec"] == 0.8
      assert load_bronco_config().blinker_mode == "auto"

    with opener.open(f"http://127.0.0.1:{port}/api/routes/00000707--adde5d06d4/0/fcamera.hevc", timeout=3) as resp:
      assert resp.read() == b"FAKE_HEVC_BYTES"
    req.add_header("Origin", "http://untrusted.example")
    with pytest.raises(urllib.error.HTTPError) as denied:
      opener.open(req, timeout=3)
    assert denied.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as denied:
      urllib.request.urlopen(f"http://127.0.0.1:{port}/api/routes/00000707--adde5d06d4/0/fcamera.hevc", timeout=3)
    assert denied.value.code == 401
  finally:
    server.shutdown()
    server.server_close()


def test_auto_lane_change_requires_signal_cancel_to_rearm():
  helper = DesireHelper()
  cfg = BroncoHubConfig(blinker_mode="auto", auto_lane_change_delay_sec=0.2)
  cs = _make_carstate(left_blinker=True)
  starts = 0
  for _ in range(100):
    before = helper.lane_change_state
    helper.update(cs, True, 0.0, config=cfg)
    starts += helper.lane_change_state == LaneChangeState.laneChangeStarting and before != helper.lane_change_state
  assert starts == 1
  # Neither temporary disengagement nor hazards rearm the automatic action.
  helper.update(cs, False, 0.0, config=cfg)
  helper.update(_make_carstate(left_blinker=True, right_blinker=True), True, 0.0, config=cfg)
  for _ in range(20):
    helper.update(cs, True, 0.0, config=cfg)
  assert helper.lane_change_state != LaneChangeState.laneChangeStarting
  helper.update(_make_carstate(), True, 0.0, config=cfg)
  for _ in range(5):
    helper.update(cs, True, 0.5, config=cfg)
  assert helper.lane_change_state == LaneChangeState.laneChangeStarting


def test_nudge_can_request_another_lane_change():
  helper = DesireHelper()
  cfg = BroncoHubConfig(blinker_mode="nudge")
  cs = _make_carstate(left_blinker=True, steering_pressed=True, steering_torque=1.0)
  helper.update(cs, True, 0.0, config=cfg)
  helper.update(cs, True, 0.0, config=cfg)
  assert helper.lane_change_state == LaneChangeState.laneChangeStarting
  for _ in range(11):
    helper.update(_make_carstate(left_blinker=True), True, 0.0, config=cfg)
  assert helper.lane_change_state == LaneChangeState.preLaneChange
  helper.update(cs, True, 0.0, config=cfg)
  assert helper.lane_change_state == LaneChangeState.laneChangeStarting


@pytest.mark.parametrize("failure", [False, True])
def test_speed_limit_invalidated_when_lookup_fails_or_is_empty(monkeypatch, failure):
  engine = NavigationEngine()
  engine.speed_limit_mps = 30.0
  engine.speed_limit_updated_at = time.monotonic()
  response = Mock()
  response.__enter__ = Mock(return_value=response)
  response.__exit__ = Mock(return_value=False)
  response.read.return_value = b'{"elements": []}'
  fetch = Mock(side_effect=OSError("offline")) if failure else Mock(return_value=response)
  monkeypatch.setattr(urllib.request, "urlopen", fetch)
  assert engine.maybe_refresh_osm_speed_limit(20.0, -103.0) == 0.0
  state = engine.update_position(20.0, -103.0)
  assert not state.has_fresh_speed_limit()


def test_speed_limit_expires_even_if_navigation_stops():
  state = BroncoNavState(speed_limit_mps=20.0, speed_limit_updated_at=100.0)
  assert state.has_fresh_speed_limit(now=101.0)
  assert not state.has_fresh_speed_limit(now=131.0)
  assert not state.has_fresh_speed_limit(now=1.0)  # previous boot's monotonic clock


def test_config_gate_requires_fresh_ignition_off_data():
  collector = BroncoTelemetryCollector(enable_ipc=False)
  assert not collector.config_changes_allowed()

  class FakeSM(dict):
    pass

  sm = FakeSM(deviceState=SimpleNamespace(started=False), pandaStates=[SimpleNamespace(ignitionLine=False, ignitionCan=False)])
  sm.seen = sm.valid = dict.fromkeys(sm, True)
  sm.recv_time = dict.fromkeys(sm, time.monotonic())
  collector.sm = sm
  assert collector.config_changes_allowed()
  sm["pandaStates"][0].ignitionCan = True
  assert not collector.config_changes_allowed()
  sm["pandaStates"][0].ignitionCan = False
  sm["deviceState"].started = True
  assert not collector.config_changes_allowed()
  sm["deviceState"].started = False
  sm.recv_time["deviceState"] -= 10
  assert not collector.config_changes_allowed()


def test_pairing_key_persists_with_private_permissions(tmp_path):
  from openpilot.selfdrive.bronco_hub.auth import load_pairing_key
  key = load_pairing_key()
  assert len(key) >= 32
  assert load_pairing_key() == key
  assert (tmp_path / "key").stat().st_mode & 0o777 == 0o600


def test_can_uses_dedicated_nonconflated_socket(monkeypatch):
  from opendbc.can import CANPacker
  import openpilot.cereal

  messaging = SimpleNamespace(SubMaster=Mock(return_value=SimpleNamespace()), sub_sock=Mock(return_value=object()))
  monkeypatch.setitem(sys.modules, "openpilot.cereal.messaging", messaging)
  monkeypatch.setattr(openpilot.cereal, "messaging", messaging, raising=False)
  monkeypatch.setitem(sys.modules, "openpilot.common.params", SimpleNamespace(Params=Mock()))
  collector = BroncoTelemetryCollector(enable_ipc=True)
  assert collector.sm is not None
  assert "can" not in messaging.SubMaster.call_args.args[0]
  messaging.sub_sock.assert_called_once_with("can", conflate=False)
  packer = CANPacker("ford_lincoln_base_pt")
  events = []
  for name, values in [("WheelSpeed", {"WhlFl_W_Meas": 10}), ("EngVehicleSpThrottle", {"ApedPos_Pc_ActlArb": 20})]:
    address, data, bus = packer.make_can_msg(name, 0, values)
    events.append(SimpleNamespace(logMonoTime=1_000_000_000, can=[SimpleNamespace(address=address, dat=data, src=bus)]))
  collector._update_extended_can(events)
  assert collector._extended_can["wheel_speeds_kph"]["fl"] > 0
  assert collector._extended_can["throttle_pct"] > 0


def test_pause_updates_ui_status_without_changing_engagement():
  # Run the actual UI state method without initializing a GPU or native IPC.
  import ast
  from enum import Enum
  source = Path("openpilot/selfdrive/ui/ui_state.py").read_text()
  tree = ast.parse(source)
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "UIState")
  method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_update_status")
  status = Enum("UIStatus", "ENGAGED DISENGAGED OVERRIDE")
  namespace = {"log": log, "UIStatus": status, "should_pause_lateral_on_blinker": should_pause_lateral_on_blinker}
  exec(compile(ast.Module(body=[method], type_ignores=[]), "ui_state.py", "exec"), namespace)

  class FakeSM(dict):
    frame = 10
    updated = {"selfdriveState": True, "carControl": True}

  sm = FakeSM(selfdriveState=SimpleNamespace(active=True, enabled=True, state=log.SelfdriveState.OpenpilotState.enabled),
              carControl=SimpleNamespace(latActive=False), carState=_make_carstate(left_blinker=True))
  ui = SimpleNamespace(_bronco_store=SimpleNamespace(refresh=lambda: (BroncoHubConfig(blinker_mode="pause"), None)),
                       started=True, sm=sm, engaged=True, _engaged_prev=True, _started_prev=True)
  namespace["_update_status"](ui)
  assert ui.blinker_steering_paused
  assert ui.status == status.OVERRIDE
  assert sm["selfdriveState"].enabled
  sm["carState"].leftBlinker = False
  sm["carControl"].latActive = True
  namespace["_update_status"](ui)
  assert not ui.blinker_steering_paused
  assert ui.status == status.ENGAGED
