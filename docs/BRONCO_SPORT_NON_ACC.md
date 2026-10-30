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
