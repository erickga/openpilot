#!/usr/bin/env python3
from dataclasses import asdict
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import threading
import time
import urllib.parse

from openpilot.selfdrive.bronco_hub.auth import PAIRING_HTML, load_pairing_key
from openpilot.selfdrive.bronco_hub.nav_engine import NavigationEngine, resolve_destination_input
from openpilot.selfdrive.bronco_hub.telemetry import (
  DEFAULT_REALDATA_DIR,
  BroncoTelemetryCollector,
  list_recorded_routes,
  resolve_segment_file,
)
from openpilot.selfdrive.controls.lib.bronco_config import load_bronco_config, save_bronco_config

DEFAULT_PORT = 8088

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Bronco Hub • comma 3X</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin=""/>
<style>
  :root {
    --bg: #0b1017;
    --card: #141d29;
    --card-border: #233246;
    --text: #f0f6fc;
    --muted: #8b9bb4;
    --accent: #2ea043;
    --amber: #f0883e;
    --danger: #f85149;
    --blue: #388bfd;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding-bottom: 76px;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    background: #101823;
    border-bottom: 1px solid var(--card-border);
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .brand { font-weight: 700; font-size: 17px; letter-spacing: 0.4px; }
  .pill {
    font-size: 12px;
    font-weight: 700;
    padding: 4px 10px;
    border-radius: 999px;
    background: #21262d;
    color: var(--muted);
  }
  .pill.engaged { background: rgba(46,160,67,0.25); color: #56d364; border: 1px solid #2ea043; }
  .pill.warn { background: rgba(240,136,62,0.25); color: #ffa657; border: 1px solid #f0883e; }
  .container { max-width: 760px; margin: 0 auto; padding: 12px; }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
  .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin-bottom: 10px; }
  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 10px; }
  .card {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 12px;
    margin-bottom: 10px;
  }
  .card-title { font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px; color: var(--muted); margin-bottom: 6px; }
  .big-val { font-size: 32px; font-weight: 800; line-height: 1.1; }
  .sub-val { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .bar-wrap { height: 8px; background: #0d1117; border-radius: 4px; overflow: hidden; margin-top: 6px; }
  .bar-fill { height: 100%; background: var(--blue); width: 0%; transition: width 0.1s linear; }
  .bsm-row { display: flex; gap: 10px; margin-bottom: 10px; }
  .bsm-box {
    flex: 1; text-align: center; padding: 10px; border-radius: 10px;
    background: #141d29; border: 1px solid var(--card-border); font-weight: 700; font-size: 13px;
  }
  .bsm-box.alert { background: rgba(248,81,73,0.28); border-color: var(--danger); color: #ff7b72; }
  .nav-banner {
    background: linear-gradient(135deg, #132a1c, #141d29);
    border: 1px solid #2ea043;
    border-radius: 12px;
    padding: 14px;
    margin-bottom: 10px;
  }
  .input-row { display: flex; gap: 8px; margin-top: 8px; }
  input[type="text"], input[type="number"], select {
    width: 100%; background: #0b1017; border: 1px solid var(--card-border);
    color: var(--text); padding: 10px 12px; border-radius: 8px; font-size: 14px;
  }
  button {
    background: var(--blue); color: #fff; border: none; border-radius: 8px;
    padding: 10px 14px; font-weight: 600; font-size: 14px; cursor: pointer;
  }
  button.secondary { background: #21262d; color: var(--text); border: 1px solid var(--card-border); }
  button.danger { background: var(--danger); }
  #map { height: 270px; width: 100%; border-radius: 10px; margin-top: 10px; background: #0d1117; }
  .setting-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 12px 0; border-bottom: 1px solid var(--card-border);
  }
  .setting-row:last-child { border-bottom: none; }
  .setting-label { font-weight: 600; font-size: 14px; }
  .setting-desc { font-size: 12px; color: var(--muted); margin-top: 2px; }
  nav.bottom-nav {
    position: fixed; bottom: 0; left: 0; right: 0;
    background: #101823; border-top: 1px solid var(--card-border);
    display: flex; justify-content: space-around; padding: 8px 0; z-index: 100;
  }
  nav.bottom-nav button {
    background: transparent; color: var(--muted); font-size: 12px; padding: 6px 12px;
  }
  nav.bottom-nav button.active { color: #58a6ff; font-weight: 700; }
  .step-item { padding: 8px 0; border-bottom: 1px solid var(--card-border); font-size: 13px; display: flex; justify-content: space-between; }
  .route-item { padding: 10px 0; border-bottom: 1px solid var(--card-border); font-size: 13px; }
  .chip-links a {
    display: inline-block; margin: 4px 6px 0 0; padding: 4px 8px;
    background: #21262d; color: #58a6ff; border-radius: 6px; text-decoration: none; font-size: 12px;
  }
</style>
</head>
<body>
<header>
  <div class="brand">BRONCO HUB • 3X</div>
  <div id="op-status-pill" class="pill">STANDBY</div>
</header>

<div class="container">
  <!-- TAB 1: LIVE COCKPIT -->
  <div id="tab-live" class="tab-panel active">
    <div id="curve-alert-box" class="nav-banner" style="display:none; border-color:var(--amber); background:#2b1911;">
      <div style="font-weight:800; color:#ffa657; font-size:16px;" id="curve-alert-text">CURVE AHEAD</div>
    </div>

    <div class="bsm-row">
      <div id="bsm-left" class="bsm-box">◀ LEFT BSM CLEAR</div>
      <div id="bsm-right" class="bsm-box">RIGHT BSM CLEAR ▶</div>
    </div>

    <div class="grid-3">
      <div class="card">
        <div class="card-title">Speed</div>
        <div class="big-val" id="val-speed">0</div>
        <div class="sub-val" id="val-gear">GEAR: P • 0 RPM</div>
      </div>
      <div class="card">
        <div class="card-title">Steering (°)</div>
        <div class="big-val" id="val-steer">0.0°</div>
        <div class="sub-val" id="val-steer-req">Target: 0.0°</div>
      </div>
      <div class="card">
        <div class="card-title">Cruise / Limit</div>
        <div class="big-val" id="val-cruise">OFF</div>
        <div class="sub-val" id="val-limit">OSM Limit: --</div>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-title">Throttle Pedal (%)</div>
        <div class="big-val" id="val-throttle">0%</div>
        <div class="bar-wrap"><div id="bar-throttle" class="bar-fill" style="background:#2ea043;"></div></div>
      </div>
      <div class="card">
        <div class="card-title">Brake Torque (Nm)</div>
        <div class="big-val" id="val-brake">0 Nm</div>
        <div class="bar-wrap"><div id="bar-brake" class="bar-fill" style="background:#f85149;"></div></div>
      </div>
    </div>

    <div class="grid-3">
      <div class="card">
        <div class="card-title">Pitch / Roll</div>
        <div class="big-val" style="font-size:22px;" id="val-inclinometer">0.0° / 0.0°</div>
        <div class="sub-val" id="val-latg">Lat G: 0.00g</div>
      </div>
      <div class="card">
        <div class="card-title">EPS Torque / Amps</div>
        <div class="big-val" style="font-size:22px;" id="val-epas">0.0 Nm</div>
        <div class="sub-val" id="val-epas-amps">0.0 A • 0.0 V</div>
      </div>
      <div class="card">
        <div class="card-title">12V Batt / CPU</div>
        <div class="big-val" style="font-size:22px;" id="val-batt">-- V</div>
        <div class="sub-val" id="val-cpu">CPU: --°C</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">4x Wheel Speeds (MPH) & Tire Pressures (PSI)</div>
      <div class="grid-4">
        <div><div class="sub-val">FL</div><div style="font-weight:700;" id="ws-fl">0.0</div><div class="sub-val" id="tp-lf">-- psi</div></div>
        <div><div class="sub-val">FR</div><div style="font-weight:700;" id="ws-fr">0.0</div><div class="sub-val" id="tp-rf">-- psi</div></div>
        <div><div class="sub-val">RL</div><div style="font-weight:700;" id="ws-rl">0.0</div><div class="sub-val" id="tp-lr">-- psi</div></div>
        <div><div class="sub-val">RR</div><div style="font-weight:700;" id="ws-rr">0.0</div><div class="sub-val" id="tp-rr">-- psi</div></div>
      </div>
    </div>
  </div>

  <!-- TAB 2: MAPS & TURN BRIDGE -->
  <div id="tab-nav" class="tab-panel">
    <div id="active-nav-card" class="nav-banner" style="display:none;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <div style="font-size:12px; color:#56d364; font-weight:700;" id="nav-dest-title">ACTIVE ROUTE</div>
          <div style="font-size:22px; font-weight:800; margin-top:2px;" id="nav-next-instruction">Continue</div>
          <div class="sub-val" id="nav-eta-sub">--</div>
        </div>
        <button class="danger" onclick="clearRoute()">Stop</button>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Send Destination from Google Maps / Apple Maps / Waze or Address</div>
      <div class="input-row">
        <input type="text" id="nav-input" placeholder="Paste Google/Apple/Waze link, lat,lon, or address...">
        <button onclick="submitDestination()">Go</button>
      </div>
      <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px;">
        <span class="sub-val" id="nav-status-msg">Tip: Tap anywhere on the map below or paste a Share link.</span>
        <button class="secondary" style="padding:6px 10px; font-size:12px;" onclick="usePhoneGps()">Sync Phone GPS</button>
      </div>
      <div id="map"></div>
    </div>

    <div class="card">
      <div class="card-title">Upcoming Turn Steps</div>
      <div id="nav-steps-list"><div class="sub-val">No active route.</div></div>
    </div>
  </div>

  <!-- TAB 3: DRIVING CONTROLS -->
  <div id="tab-controls" class="tab-panel">
    <div class="card">
      <div class="card-title">Lateral & Blinker Behavior (Ignition Off Only)</div>
      <p id="config-status" role="status">Turn ignition off before changing settings.</p>

      <div class="setting-row">
        <div>
          <div class="setting-label">Blinker Action Mode</div>
          <div class="setting-desc">Choose Standard Nudge, Highway Auto Lane Change, or Pause Steering on Blinker.</div>
        </div>
        <select id="cfg-blinker_mode" style="width:165px;" onchange="saveCfg()">
          <option value="nudge">Standard Nudge</option>
          <option value="auto">Auto Lane Change</option>
          <option value="pause">Pause on Blinker</option>
        </select>
      </div>

      <div class="setting-row">
        <div>
          <div class="setting-label">Auto Lane Change Delay (sec)</div>
          <div class="setting-desc">Wait time with clear Blind-Spot radar before auto lane change starts.</div>
        </div>
        <input type="number" id="cfg-auto_lane_change_delay_sec" step="0.1" min="0.2" max="2.5" style="width:90px;" onchange="saveCfg()">
      </div>

      <div class="setting-row">
        <div>
          <div class="setting-label">City Pause Lateral Below Speed (MPH)</div>
          <div class="setting-desc">Automatically pause steering torque when blinker is on below this speed (0 = off).</div>
        </div>
        <input type="number" id="cfg-pause_lateral_below_mph" step="5" min="0" max="45" style="width:90px;" onchange="saveCfg()">
      </div>

      <div class="setting-row">
        <div>
          <div class="setting-label">Blind-Spot Red Lane Line Glow</div>
          <div class="setting-desc">Highlight left/right lane line in orange-red on the comma screen when BSM detects a vehicle.</div>
        </div>
        <input type="checkbox" id="cfg-blindspot_path_glow" onchange="saveCfg()" style="width:22px; height:22px;">
      </div>

      <div class="setting-row">
        <div>
          <div class="setting-label">Road-Edge Lane Change Protection</div>
          <div class="setting-desc">Block auto lane change if camera detects a curb or road edge on the target side.</div>
        </div>
        <input type="checkbox" id="cfg-road_edge_lane_change_block" onchange="saveCfg()" style="width:22px; height:22px;">
      </div>

      <div class="setting-row">
        <div>
          <div class="setting-label">Vision Curve Speed Warning</div>
          <div class="setting-desc">Show recommended turn speed alert on comma HUD and phone before sharp curves.</div>
        </div>
        <input type="checkbox" id="cfg-curve_speed_warning" onchange="saveCfg()" style="width:22px; height:22px;">
      </div>

      <div class="setting-row">
        <div>
          <div class="setting-label">Show Navigation & OSM Speed Limit on Comma HUD</div>
          <div class="setting-desc">Display upcoming turn banner and OpenStreetMap speed limit sign on the comma 3X screen.</div>
        </div>
        <input type="checkbox" id="cfg-hud_show_nav" onchange="saveCfg()" style="width:22px; height:22px;">
      </div>
    </div>
  </div>

  <!-- TAB 4: DIAGNOSTICS & DASHCAM -->
  <div id="tab-diag" class="tab-panel">
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div class="card-title">Bronco Sport ECU & Safety Health</div>
        <button class="secondary" style="padding:5px 10px; font-size:12px;" onclick="loadDiagnostics()">Refresh</button>
      </div>
      <div id="diag-checks-list" style="margin-top:8px;"></div>
    </div>

    <div class="card">
      <div class="card-title">Discovered Ford ECU Firmware Versions</div>
      <div id="diag-ecus-list"><div class="sub-val">Loading...</div></div>
    </div>

    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div class="card-title">Recorded Dashcam Routes (/data/media/0/realdata)</div>
        <button class="secondary" style="padding:5px 10px; font-size:12px;" onclick="loadRoutes()">Refresh</button>
      </div>
      <div id="routes-list" style="margin-top:8px;"><div class="sub-val">Loading routes...</div></div>
    </div>
  </div>
</div>

<nav class="bottom-nav">
  <button id="btn-live" class="active" onclick="switchTab('live')">Live Gauges</button>
  <button id="btn-nav" onclick="switchTab('nav')">Maps & Turns</button>
  <button id="btn-controls" onclick="switchTab('controls')">Controls</button>
  <button id="btn-diag" onclick="switchTab('diag')">ECU & Dashcam</button>
</nav>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
<script>
function escapeHtml(value) {
  const el = document.createElement('span');
  el.textContent = String(value);
  return el.innerHTML;
}
let map = null, carMarker = null, routeLine = null;
let lastGps = {lat: 20.6736, lon: -103.344};

function switchTab(tab) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav.bottom-nav button').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  document.getElementById('btn-' + tab).classList.add('active');
  if (tab === 'nav') initMap();
  if (tab === 'controls') loadCfg();
  if (tab === 'diag') { loadDiagnostics(); loadRoutes(); }
}

function initMap() {
  if (map) { setTimeout(() => map.invalidateSize(), 100); return; }
  if (typeof L === 'undefined') return;
  map = L.map('map').setView([lastGps.lat, lastGps.lon], 14);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '© OpenStreetMap'
  }).addTo(map);
  carMarker = L.circleMarker([lastGps.lat, lastGps.lon], {radius: 8, color: '#388bfd', fillOpacity: 0.9}).addTo(map);
  map.on('click', function(e) {
    document.getElementById('nav-input').value = e.latlng.lat.toFixed(5) + ', ' + e.latlng.lng.toFixed(5);
    submitDestination();
  });
}

function renderTelemetry(d) {
  const v = d.vehicle || {};
  const f = d.ford_can || {};
  const op = d.openpilot || {};
  const nav = d.nav || {};
  const curve = d.curve || {};

  const pill = document.getElementById('op-status-pill');
  if (op.lat_active) {
    pill.textContent = 'STEERING ACTIVE';
    pill.className = 'pill engaged';
  } else if (op.enabled) {
    pill.textContent = 'PAUSED (BLINKER)';
    pill.className = 'pill warn';
  } else {
    pill.textContent = v.cruise_available ? 'CRUISE STANDBY' : 'DISENGAGED';
    pill.className = 'pill';
  }

  document.getElementById('val-speed').textContent = Math.round(v.speed_mph || 0) + ' mph';
  const gNum = f.gear_num ? (' (G' + f.gear_num + ')') : '';
  document.getElementById('val-gear').textContent = 'GEAR: ' + (v.gear || 'P').toUpperCase() + gNum + ' • ' + Math.round(f.rpm || 0) + ' RPM';
  document.getElementById('val-steer').textContent = (v.steering_angle_deg || 0).toFixed(1) + '°';
  document.getElementById('val-steer-req').textContent = 'Target: ' + (v.desired_angle_deg || 0).toFixed(1) + '°';

  if (v.cruise_enabled) {
    document.getElementById('val-cruise').textContent = Math.round(v.cruise_set_mph || 0) + ' mph';
  } else if (v.cruise_available) {
    document.getElementById('val-cruise').textContent = 'GRAY ON';
  } else {
    document.getElementById('val-cruise').textContent = 'OFF';
  }

  const limMph = nav.speed_limit_mps > 0 ? Math.round(nav.speed_limit_mps * 2.23694) + ' mph' : '--';
  document.getElementById('val-limit').textContent = 'OSM Limit: ' + limMph;

  const th = Math.min(100, Math.max(0, f.throttle_pct || 0));
  document.getElementById('val-throttle').textContent = th.toFixed(0) + '%';
  document.getElementById('bar-throttle').style.width = th + '%';

  const brk = Math.max(0, f.brake_torque_nm || 0);
  document.getElementById('val-brake').textContent = Math.round(brk) + ' Nm';
  document.getElementById('bar-brake').style.width = Math.min(100, brk / 15) + '%';

  document.getElementById('val-inclinometer').textContent = (v.pitch_deg || 0).toFixed(1) + '° / ' + (v.roll_deg || 0).toFixed(1) + '°';
  document.getElementById('val-latg').textContent = 'Lat G: ' + (v.lat_g || 0).toFixed(2) + 'g';
  document.getElementById('val-epas').textContent = (v.steering_torque_nm || 0).toFixed(1) + ' Nm';
  document.getElementById('val-epas-amps').textContent = (f.eps_current_a || 0).toFixed(1) + ' A • ' + (f.eps_voltage_v || 0).toFixed(1) + ' V';
  document.getElementById('val-batt').textContent = (op.battery_v || 0).toFixed(2) + ' V';
  document.getElementById('val-cpu').textContent = 'CPU: ' + (op.cpu_temp_c || 0).toFixed(1) + '°C';

  const bsmL = document.getElementById('bsm-left');
  bsmL.className = (v.left_blindspot || f.cta_left) ? 'bsm-box alert' : 'bsm-box';
  bsmL.textContent = (v.left_blindspot || f.cta_left) ? '⚠️ CAR IN LEFT BLIND SPOT' : '◀ LEFT BSM CLEAR';

  const bsmR = document.getElementById('bsm-right');
  bsmR.className = (v.right_blindspot || f.cta_right) ? 'bsm-box alert' : 'bsm-box';
  bsmR.textContent = (v.right_blindspot || f.cta_right) ? 'CAR IN RIGHT BLIND SPOT ⚠️' : 'RIGHT BSM CLEAR ▶';

  const ws = f.wheel_speeds_kph || {};
  document.getElementById('ws-fl').textContent = ((ws.fl || 0) * 0.621371).toFixed(1);
  document.getElementById('ws-fr').textContent = ((ws.fr || 0) * 0.621371).toFixed(1);
  document.getElementById('ws-rl').textContent = ((ws.rl || 0) * 0.621371).toFixed(1);
  document.getElementById('ws-rr').textContent = ((ws.rr || 0) * 0.621371).toFixed(1);

  const tp = f.tire_press_kpa || {};
  document.getElementById('tp-lf').textContent = tp.lf ? (tp.lf * 0.145038).toFixed(1) + ' psi' : '-- psi';
  document.getElementById('tp-rf').textContent = tp.rf ? (tp.rf * 0.145038).toFixed(1) + ' psi' : '-- psi';
  document.getElementById('tp-lr').textContent = tp.lr ? (tp.lr * 0.145038).toFixed(1) + ' psi' : '-- psi';
  document.getElementById('tp-rr').textContent = tp.rr ? (tp.rr * 0.145038).toFixed(1) + ' psi' : '-- psi';

  const cbox = document.getElementById('curve-alert-box');
  if (curve.warning_active) {
    cbox.style.display = 'block';
    const cDist = Math.round(curve.peak_distance_m);
    document.getElementById('curve-alert-text').textContent =
      '⚠️ SHARP CURVE IN ' + cDist + 'm • SLOW TO ' + curve.recommended_speed_mph + ' MPH';
  } else {
    cbox.style.display = 'none';
  }

  if (d.gps && d.gps.has_fix) {
    lastGps = {lat: d.gps.lat, lon: d.gps.lon};
    if (carMarker) carMarker.setLatLng([lastGps.lat, lastGps.lon]);
  }

  const navCard = document.getElementById('active-nav-card');
  if (nav.active) {
    navCard.style.display = 'block';
    document.getElementById('nav-dest-title').textContent = 'TO: ' + (nav.destination_name || 'Destination');
    const nDist = Math.round(nav.distance_to_next_m || 0);
    document.getElementById('nav-next-instruction').textContent =
      nDist + ' m • ' + (nav.next_maneuver_instruction || 'Continue');
    const remKm = ((nav.distance_remaining_m || 0) / 1000).toFixed(1);
    const remMin = Math.ceil((nav.duration_remaining_s || 0) / 60);
    document.getElementById('nav-eta-sub').textContent = remKm + ' km remaining • ~' + remMin + ' min';
  } else {
    navCard.style.display = 'none';
  }
}

async function submitDestination() {
  const query = document.getElementById('nav-input').value.trim();
  if (!query) return;
  document.getElementById('nav-status-msg').textContent = 'Resolving destination & calculating route...';
  try {
    const res = await fetch('/api/nav/destination', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query: query, start_lat: lastGps.lat, start_lon: lastGps.lon})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Route error');
    document.getElementById('nav-status-msg').textContent = 'Route active to ' + data.route.destination_name;
    drawRouteOnMap(data.route);
  } catch (e) {
    document.getElementById('nav-status-msg').textContent = 'Error: ' + e.message;
  }
}

function drawRouteOnMap(rt) {
  if (map && rt.geometry && rt.geometry.length) {
    if (routeLine) map.removeLayer(routeLine);
    routeLine = L.polyline(rt.geometry, {color: '#2ea043', weight: 5}).addTo(map);
    map.fitBounds(routeLine.getBounds(), {padding: [20, 20]});
  }
  const list = document.getElementById('nav-steps-list');
  if (rt.steps && rt.steps.length) {
    list.innerHTML = rt.steps.map((s, idx) =>
      `<div class="step-item"><span>${idx + 1}. ${escapeHtml(s.instruction)}</span><span style="color:var(--muted)">${Math.round(s.distance_m)} m</span></div>`
    ).join('');
  }
}

async function clearRoute() {
  await fetch('/api/nav/clear', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
  if (map && routeLine) { map.removeLayer(routeLine); routeLine = null; }
  document.getElementById('nav-steps-list').innerHTML = '<div class="sub-val">No active route.</div>';
}

function usePhoneGps() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(async pos => {
    lastGps = {lat: pos.coords.latitude, lon: pos.coords.longitude};
    if (map) { map.setView([lastGps.lat, lastGps.lon], 15); carMarker.setLatLng([lastGps.lat, lastGps.lon]); }
    await fetch('/api/nav/gps', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({lat: lastGps.lat, lon: lastGps.lon, speed_mps: pos.coords.speed || 0})
    });
    document.getElementById('nav-status-msg').textContent = 'Synced GPS from phone: ' + lastGps.lat.toFixed(5) + ', ' + lastGps.lon.toFixed(5);
  });
}

async function loadCfg() {
  const res = await fetch('/api/config');
  const cfg = await res.json();
  document.getElementById('cfg-blinker_mode').value = cfg.blinker_mode || 'nudge';
  document.getElementById('cfg-auto_lane_change_delay_sec').value = cfg.auto_lane_change_delay_sec;
  document.getElementById('cfg-pause_lateral_below_mph').value = cfg.pause_lateral_below_mph;
  document.getElementById('cfg-blindspot_path_glow').checked = !!cfg.blindspot_path_glow;
  document.getElementById('cfg-road_edge_lane_change_block').checked = !!cfg.road_edge_lane_change_block;
  document.getElementById('cfg-curve_speed_warning').checked = !!cfg.curve_speed_warning;
  document.getElementById('cfg-hud_show_nav').checked = !!cfg.hud_show_nav;
}

async function saveCfg() {
  const payload = {
    blinker_mode: document.getElementById('cfg-blinker_mode').value,
    auto_lane_change_delay_sec: parseFloat(document.getElementById('cfg-auto_lane_change_delay_sec').value || 0.6),
    pause_lateral_below_mph: parseFloat(document.getElementById('cfg-pause_lateral_below_mph').value || 0),
    blindspot_path_glow: document.getElementById('cfg-blindspot_path_glow').checked,
    road_edge_lane_change_block: document.getElementById('cfg-road_edge_lane_change_block').checked,
    curve_speed_warning: document.getElementById('cfg-curve_speed_warning').checked,
    hud_show_nav: document.getElementById('cfg-hud_show_nav').checked,
  };
  const res = await fetch('/api/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  const result = await res.json();
  document.getElementById('config-status').textContent = res.ok ? 'Settings saved.' : (result.error || 'Settings not saved.');
  if (!res.ok) await loadCfg();
}

async function loadDiagnostics() {
  const res = await fetch('/api/diagnostics');
  const d = await res.json();
  document.getElementById('diag-checks-list').innerHTML = (d.fault_checks || []).map(c =>
    `<div class="step-item"><span>${c.ok ? '✅' : '⚠️'} ${escapeHtml(c.name)}</span><span style="color:var(--muted)">${escapeHtml(c.detail)}</span></div>`
  ).join('');
  const ecus = d.ecus || [];
  document.getElementById('diag-ecus-list').innerHTML = ecus.length ? ecus.map(e =>
    `<div class="step-item"><span><b>${escapeHtml(e.ecu)}</b> (${escapeHtml(e.address)})</span>
      <span style="font-family:monospace; color:#58a6ff;">${escapeHtml(e.fw_version)}</span></div>`
  ).join('') : '<div class="sub-val">Turn vehicle ignition ON to populate live CarParams ECU inventory.</div>';
}

async function loadRoutes() {
  const res = await fetch('/api/routes');
  const data = await res.json();
  const routes = data.routes || [];
  const el = document.getElementById('routes-list');
  if (!routes.length) {
    el.innerHTML = '<div class="sub-val">No recorded routes found in /data/media/0/realdata.</div>';
    return;
  }
  el.innerHTML = routes.map(r => {
    const segLinks = (r.segments || []).slice(-4).map(s =>
      (s.files || []).map(f =>
        `<a href="/api/routes/${r.route_id}/${s.segment}/${f.name}" download="${r.route_id}--${s.segment}--${f.name}">Seg ${s.segment}: ${f.name}</a>`
      ).join('')
    ).join('');
    return `<div class="route-item">
      <div style="font-weight:700;">Route ${r.route_id}
        <span style="color:var(--muted); font-weight:400;">(${r.segment_count} segments • ${r.total_size_mb} MB)</span>
      </div>
      <div class="chip-links">${segLinks}</div>
    </div>`;
  }).join('');
}

function connectStream() {
  const es = new EventSource('/api/stream');
  es.onmessage = ev => {
    try { renderTelemetry(JSON.parse(ev.data)); } catch (e) {}
  };
  es.onerror = () => {
    es.close();
    setTimeout(connectStream, 2000);
  };
}
connectStream();
</script>
</body>
</html>
"""


class BroncoHubRuntime:
  def __init__(self, enable_ipc: bool = True, realdata_dir: str | Path = DEFAULT_REALDATA_DIR):
    self.pairing_key = load_pairing_key()
    self.session_token = secrets.token_urlsafe(32)
    self.collector = BroncoTelemetryCollector(enable_ipc=enable_ipc)
    self.nav_engine = NavigationEngine()
    self.realdata_dir = Path(realdata_dir)
    self._lock = threading.Lock()
    self._latest_snapshot = self.collector.update_and_snapshot()

  def step(self) -> dict:
    with self._lock:
      snap = self.collector.update_and_snapshot()
      gps = snap.get("gps") or {}
      if gps.get("has_fix"):
        lat, lon = float(gps["lat"]), float(gps["lon"])
        v_ego = float(snap["vehicle"]["speed_mps"])
        self.nav_engine.maybe_refresh_osm_speed_limit(lat, lon)
        nav_state = self.nav_engine.update_position(lat, lon, v_ego)
        snap["nav"] = asdict(nav_state)
      self._latest_snapshot = snap
      return snap

  def get_snapshot(self) -> dict:
    with self._lock:
      return self._latest_snapshot


def make_handler(runtime: BroncoHubRuntime):
  class BroncoHubHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
      return

    def _authenticated(self) -> bool:
      cookie = SimpleCookie()
      try:
        cookie.load(self.headers.get("Cookie", ""))
        session = cookie.get("bronco_session")
        return session is not None and secrets.compare_digest(session.value, runtime.session_token)
      except (ValueError, TypeError, CookieError):
        return False

    def _require_auth(self) -> bool:
      if self._authenticated():
        return True
      self._send_json({"error": "Pair this browser with Bronco Hub first."}, HTTPStatus.UNAUTHORIZED)
      return False

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK, cookie: str | None = None) -> None:
      body = json.dumps(payload).encode("utf-8")
      self.send_response(status)
      self.send_header("Content-Type", "application/json; charset=utf-8")
      self.send_header("Content-Length", str(len(body)))
      self.send_header("Cache-Control", "no-store")
      if cookie is not None:
        self.send_header("Set-Cookie", cookie)
      self.end_headers()
      self.wfile.write(body)

    def _read_json_body(self) -> dict:
      length = int(self.headers.get("Content-Length", "0"))
      if length <= 0 or length > 65536:
        return {}
      raw = self.rfile.read(length).decode("utf-8")
      body = json.loads(raw) if raw else {}
      if not isinstance(body, dict):
        raise ValueError("Expected a JSON object")
      return body

    def do_GET(self) -> None:
      parsed = urllib.parse.urlparse(self.path)
      path = parsed.path

      if path in ("/", "/index.html"):
        body = (INDEX_HTML if self._authenticated() else PAIRING_HTML).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)
        return

      if not self._require_auth():
        return

      if path == "/api/telemetry":
        self._send_json(runtime.get_snapshot())
        return

      if path == "/api/config":
        self._send_json(asdict(load_bronco_config()))
        return

      if path == "/api/nav/status":
        self._send_json(runtime.nav_engine.to_dict())
        return

      if path == "/api/diagnostics":
        self._send_json(runtime.collector.get_diagnostics_summary())
        return

      if path == "/api/routes":
        self._send_json({"routes": list_recorded_routes(runtime.realdata_dir)})
        return

      if path.startswith("/api/routes/"):
        parts = [p for p in path.split("/") if p]
        if len(parts) == 5:
          _, _, route_id, seg_str, filename = parts
          try:
            seg_num = int(seg_str)
          except ValueError:
            self._send_json({"error": "Invalid segment"}, HTTPStatus.BAD_REQUEST)
            return
          fpath = resolve_segment_file(runtime.realdata_dir, route_id, seg_num, filename)
          if fpath is None:
            self._send_json({"error": "File not found"}, HTTPStatus.NOT_FOUND)
            return
          ctype, _ = mimetypes.guess_type(fpath.name)
          size = fpath.stat().st_size
          self.send_response(HTTPStatus.OK)
          self.send_header("Content-Type", ctype or "application/octet-stream")
          self.send_header("Content-Length", str(size))
          self.send_header("Content-Disposition", f'attachment; filename="{route_id}--{seg_num}--{filename}"')
          self.end_headers()
          with open(fpath, "rb") as f:
            while chunk := f.read(65536):
              self.wfile.write(chunk)
          return

      if path == "/api/stream":
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
          while True:
            snap = runtime.get_snapshot()
            msg = f"data: {json.dumps(snap)}\n\n".encode()
            self.wfile.write(msg)
            self.wfile.flush()
            time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError):
          return

      self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
      parsed = urllib.parse.urlparse(self.path)
      path = parsed.path

      # JSON-only mutations and same-origin checks prevent form/CSRF submissions.
      origin = self.headers.get("Origin")
      if origin and origin != "http://" + self.headers.get("Host", ""):
        self._send_json({"error": "Cross-origin requests are not allowed."}, HTTPStatus.FORBIDDEN)
        return
      if self.headers.get_content_type() != "application/json":
        self._send_json({"error": "Expected application/json."}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        return
      if path != "/api/pair" and not self._require_auth():
        return
      try:
        body = self._read_json_body()
      except Exception:
        self._send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
        return

      if path == "/api/pair":
        key = body.get("key")
        if not isinstance(key, str) or not secrets.compare_digest(key.encode("utf-8"), runtime.pairing_key.encode("utf-8")):
          self._send_json({"error": "Invalid pairing key."}, HTTPStatus.UNAUTHORIZED)
          return
        self._send_json({"status": "paired"}, cookie=f"bronco_session={runtime.session_token}; HttpOnly; SameSite=Strict; Path=/")
        return

      if path == "/api/config":
        if not runtime.collector.config_changes_allowed():
          self._send_json({"error": "Turn ignition off and wait for fresh device status before changing settings."}, HTTPStatus.CONFLICT)
          return
        try:
          saved = save_bronco_config(body)
        except (TypeError, ValueError):
          self._send_json({"error": "Invalid configuration values."}, HTTPStatus.BAD_REQUEST)
          return
        self._send_json(asdict(saved))
        return

      if path == "/api/nav/clear":
        state = runtime.nav_engine.clear_route()
        self._send_json(asdict(state))
        return

      if path == "/api/nav/gps":
        lat = float(body.get("lat", 0.0))
        lon = float(body.get("lon", 0.0))
        spd = float(body.get("speed_mps", 0.0))
        runtime.collector._last_gps = {
          "lat": lat,
          "lon": lon,
          "bearing_deg": 0.0,
          "speed_mps": spd,
          "has_fix": True,
        }
        runtime.nav_engine.maybe_refresh_osm_speed_limit(lat, lon)
        state = runtime.nav_engine.update_position(lat, lon, spd)
        self._send_json(asdict(state))
        return

      if path == "/api/nav/destination":
        query = str(body.get("query") or "").strip()
        try:
          dest_lat, dest_lon, label = resolve_destination_input(query)
          gps = runtime.collector._last_gps
          start_lat = float(body.get("start_lat") or gps.get("lat") or dest_lat)
          start_lon = float(body.get("start_lon") or gps.get("lon") or dest_lon)
          route = runtime.nav_engine.start_route(start_lat, start_lon, dest_lat, dest_lon, label)
          self._send_json({"status": "ok", "route": asdict(route)})
        except Exception as e:
          self._send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        return

      self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

  return BroncoHubHandler


def main() -> None:
  port = int(os.environ.get("BRONCO_HUB_PORT", str(DEFAULT_PORT)))
  runtime = BroncoHubRuntime(enable_ipc=True)

  def _bg_worker() -> None:
    while True:
      try:
        runtime.step()
      except Exception:
        pass
      time.sleep(0.1)

  t = threading.Thread(target=_bg_worker, daemon=True)
  t.start()

  server = ThreadingHTTPServer((os.environ.get("BRONCO_HUB_BIND", "0.0.0.0"), port), make_handler(runtime))
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    server.server_close()


if __name__ == "__main__":
  main()
