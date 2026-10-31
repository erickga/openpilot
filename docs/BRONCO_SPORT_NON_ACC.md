# Bronco Sport non-ACC port: openpilot 0.11.2

This branch ports the custom 0.11.1 behavior from `905113ec6` onto the comma 3X-compatible upstream `release-tizi` build `f7db927c728bab57bb1263e05465766a8f28fc9d`.

## Preserved driver behavior

- An observed cruise OFF -> ON transition requests steering engagement, including standby. Normal entry checks still apply.
- Cruise OFF or CANCEL disables steering. Another OFF -> ON cycle is required after a disable.
- Braking does not disengage steering in this specific mode. The driver remains responsible for speed and braking.
- Starting with cruise already ON, releasing the brake, SET/RES, or recovering from invalid input cannot automatically engage steering.
- ON while accelerator or CANCEL is held consumes and rejects the request.
- Stock cruise state remains real. SET can operate stock cruise while steering is disabled; openpilot does not automatically cancel it in this mode.
- No openpilot acceleration/braking commands or generated RES commands are allowed in this mode.
- Driver monitoring and the existing vehicle identification and EPS configuration checks remain enforced.

The mode is restricted to the known Bronco Sport non-ACC firmware variant. No vehicle programming or FORScan changes are part of this upgrade.

## Migration details

Upstream now uses Ford flag bit `2` for blind-spot monitoring. The custom flags move to `NON_ACC=4`, `MAIN_SWITCH=8`; the safety parameter remains `LATERAL_ONLY | MAIN_SWITCH = 12`. Runtime Ford flags are `12` without BSM or `14` with BSM. They must be recomputed by the new interface, not copied from old serialized CarParams.

The port uses upstream's new `openpilot/` package layout and `CarEvents`. It retains upstream 0.11.2 curvature safety limits and adds the existing independent Panda main-switch latch around them. Regression tests include the inherited curvature, rate, relay and forwarding checks, invalid mode combinations, timeout handling, and 3,000 deterministic Python/Panda driver-input sequences.

The `prebuilt` marker is removed so startup builds the modified source and matching firmware. Checked-in upstream build products are not evidence of a custom firmware build; do not bypass the build by restoring `prebuilt`.

Packaged releases contain compiled driving/monitoring models but omit their ONNX sources. The model build retains those shipped artifacts only when release metadata (`git_src_commit`) is present, the ONNX source is absent, and the compiled model exists. Native code and Panda firmware still rebuild normally. Source checkouts with ONNX files retain normal model compilation.

## Validation and deployment status

Local validation on macOS / Python 3.12:

- Ford car and Panda tests: 182 passed, 82 skipped, followed by 8 passed after adding the BSM regression (one additional test).
- Ford event generation and stock cruise cancellation: 5 passed.
- Generic Ford interface and fingerprint tests: 11 passed.
- Safety debug and release compilation: 2 passed.
- Ruff and whitespace checks passed.
- Panda H7 firmware built with ARM GCC 13.2.1 using the repository debug signing key.

These tests do not establish device runtime or physical steering performance. Event-generation tests substitute an event collector for alert rendering/IPC. Full device startup, current CAN signals, engagement/disengagement and stock SET behavior must be verified on the vehicle.

On-device validation completed on 2026-10-30 at code commit `95fa9dc53`: AGNOS 19.9 booted, the full native build completed, and pandad flashed matching firmware and selected Ford safety parameter 12. Runtime checks showed the expected Bronco identification, steering-only mode, valid CAN, no Panda faults and no displayed alert while parked. Checked persistent settings, calibration and SSH configuration matched the pre-upgrade backup. Physical button and controlled driving validation remain outstanding.

Before switching installations, retain the current code, firmware and persistent parameter backup and verify the vehicle is parked. Preserve personal settings, calibration, pairing and SSH keys, including the current `DisengageOnAccelerator` value. Do not reuse old CarParams flags. The upstream target requires AGNOS 19.9 (the prior branch targeted 18.4), so allow for the upstream OS update as well as the code build. Rollback must account for both the code and OS version, not just restore an old directory.

The earlier 0.11.1 branch remains the rollback reference.

## Bronco Hub: Wi-Fi Telemetry, Maps/Turn Bridge, and Community Lateral Controls

This branch also includes `openpilot.selfdrive.bronco_hub`, a zero-dependency web dashboard and lateral helper suite managed by `process_config.py` on port `8088`:

- **Live CAN & Cereal Telemetry (`/api/telemetry`, `/api/stream`):** Streams real-time speed, steering angle/torque, pitch/roll inclinometer, lateral G-force, individual wheel speeds (`WheelSpeed`), tire pressures (`Tire_Pressure_Data_FD1`), throttle percentage (`EngVehicleSpThrottle`), brake torque (`BrakeSnData_4`), transmission gear (`PowertrainData_10`), EPS voltage/current (`EPAS_INFO`), blind-spot/CTA status, and 12V battery voltage over Wi-Fi.
- **Maps & Turn-by-Turn Bridge (`nav_engine.py`):** Accepts Google Maps, Apple Maps, and Waze share links, coordinates, or address searches, computes turn-by-turn routes via OSRM, queries OpenStreetMap `maxspeed` tags, and renders an upcoming turn banner and speed limit sign on the comma 3X HUD (`hud_renderer.py`).
- **Configurable Blinker & Lateral Features (`bronco_config.py`, `desire_helper.py`, `controlsd.py`, `model_renderer.py`):**
  - `blinker_mode`: `"nudge"` (default), `"auto"` (highway nudge-less lane change after configurable BSM-clear and road-edge-clear delay), or `"pause"` (temporarily pauses lateral steering while blinker is active).
  - `pause_lateral_below_mph`: Optional city speed threshold below which activating a turn signal pauses steering torque.
  - `blindspot_path_glow`: Highlights the corresponding ego lane line in orange-red on the comma 3X road view (`model_renderer.py`) when the vehicle's blind-spot radar detects an adjacent car.
  - `road_edge_lane_change_block`: Prevents automatic lane changes when `modelV2` detects a road edge or curb on the target side.
  - `curve_speed_warning`: Estimates upcoming path curvature from `modelV2` and displays a recommended turn speed alert on the HUD and web dashboard when approaching a sharp curve above the configured lateral acceleration threshold.
- **ECU Diagnostics & Dashcam Browser (`/api/diagnostics`, `/api/routes`):** Displays discovered ECU firmware versions, passive CAN health checks, and segment download links for recorded routes in `/data/media/0/realdata`.


### Hub access and deployment review fixes

The Hub uses **port 8088**, not 8080. Its default bind address is `0.0.0.0`, which accepts connections on all IPv4 interfaces; actual reachability depends on routing, firewall, VPN and port-forwarding configuration. This is not an enforced Wi-Fi-only boundary. Do not forward this HTTP service to the Internet.

At first startup it generates `/data/bronco_hub_key` with owner-only permissions (0600). Retrieve that key through the device's existing SSH connection and enter it at `http://<comma-ip>:8088`. The key is not embedded in the page, URL, or logs. Every telemetry, navigation, diagnostics, configuration, stream and recording endpoint requires a paired browser session. The session cookie is HttpOnly and SameSite=Strict; sessions reset when the Hub restarts. POST endpoints require JSON and reject cross-origin browser requests. To revoke all sessions, restart the Hub; to replace the pairing key, remove the key file while the Hub is stopped and restart it.

Pairing authenticates access but **HTTP does not encrypt the key or session**. Use a trusted private LAN/hotspot. For an encrypted connection on shared networks, configure `BRONCO_HUB_BIND=127.0.0.1` in the manager's startup environment and forward the port with the existing SSH credentials:

```sh
ssh -N -L 127.0.0.1:8088:127.0.0.1:8088 <existing-ssh-user>@<comma-ip>
```

Then open `http://127.0.0.1:8088` on that SSH client. Merely using the tunnel without changing the bind address leaves the LAN listener accessible. `BRONCO_HUB_PORT` overrides the port; `BRONCO_HUB_KEY_PATH` overrides the key location. No device networking changes have been applied by the local review.

All configuration writes require fresh, valid device/Panda messages confirming ignition off. Missing or stale status denies writes. Read-only telemetry remains accessible to paired browsers onroad. The settings page reports rejected saves.

Automatic lane changes now allow only one automatic action until both blinkers are off. Keeping the signal on, toggling hazards, or temporarily losing lateral activity does not rearm it; a deliberate steering nudge can still request a subsequent change. Blinker steering pauses use the override UI status and display `STEERING PAUSED • TAKE CONTROL` instead of the curve advisory. CAN telemetry has a dedicated non-conflated subscription. Speed limits carry a separate observation timestamp, expire after 30 seconds, and clear after empty or failed map lookups; nearby-road matching is still approximate.

Local regression checks cover pairing/access restrictions, ignition-off gating, lane-change rearming, pause UI status, CAN subscription/decoding, and stale speed limits. Device build, rendered HUD inspection and parked runtime validation remain required before driving with these changes. No deployment has been performed.
