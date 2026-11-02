# Bronco early closing-speed advisory

Implemented 2026-11-02 for the non-ACC Bronco Sport port. This is a driver
advisory on the comma display/speaker, not collision avoidance or automatic braking.
It does not modify steering, Panda, factory AEB, or existing urgent FCW logic.

## Behavior

Small amber `CLOSING FAST — Check braking` banner, at most two seconds per episode,
with an initial ~0.3-second prompt sound request. Uses the existing UI/audio alert
manager at low priority; the urgent `BRAKE!` warning supersedes it. VisualAlert.none
ensures this advisory does not request a Ford CAN collision-warning display.
The alert type is `broncoClosingAdvisory` in selfdriveState. An onset diagnostic
records distance, closing speed, both acceleration estimates and brakePressed.

Default enabled for the supported non-ACC Bronco, configurable through the Hub's
`Closing Fast Advisory` checkbox (`closing_speed_warning`). Existing Hub pairing
and ignition-off configuration guards apply. It may warn while steering assistance
is disengaged, provided openpilot is initialized/non-passive and the vehicle is in
drive. Other vehicles do not activate it.

Initial eligibility requires:

- Valid CAN and a present, valid lead message no older than 0.2 seconds.
- Speed >=10 km/h; estimated lead distance >3 and <=100 m; lead probability >=0.95.
- Closing speed >=2 m/s and distance/closing speed <=5.5 seconds.
- Estimated relative deceleration below `closing_speed² / (2 * max(distance-5, 1))`.
  Observed relative deceleration is lead acceleration minus ego acceleration.
- 0.3 seconds of persistence. During dwell, a 6.5-second exit threshold and 1.2x
  relative-deceleration threshold reduce resets from camera velocity noise.

Pedal contact is diagnostic only: **brakePressed does not suppress the advisory**.
It clears when the condition improves, data becomes invalid, a large lead-distance
discontinuity occurs, or stock FCW/AEB or openpilot FCW is active. Rearming requires
two seconds clear and at least ten seconds since the preceding alert. Continuous
closing risk does not repeatedly chime. These are engineering tuning choices,
not validated safe stopping distances. The constant-relative-acceleration estimate
does not model every cut-in, slope, tire/road condition or camera error.

## Evidence and validation

The installed non-ACC car reports radarUnavailable=true and
openpilotLongitudinalControl=false. Recorded radarState leads are camera-derived;
the service name does not imply radar hardware. Ego acceleration is speed-filter
derived; the port supplies a brake pressed switch, not brake pressure.

Two completed minute-long qlog segments contained 480 valid lead samples, all
camera-backed, with reported lead probabilities 0.969–0.9999. The relevant approach
included a 48.1 km/h sample, 35.1 m estimated gap, 7.0 m/s closing speed and brake
pressed. That snapshot motivated review; it alone did not specify an alert time.

Full-rate rlog replay of the approach produces **one** candidate advisory at
43.49 km/h, 25.64 m estimated gap, 5.73 m/s closing speed, estimated ego acceleration
-1.517 m/s² and lead acceleration -1.083 m/s², with brake pressed. Constant-speed
TTC is 4.47 s. This is a warning during an already-braking approach, not evidence
that it precedes the driver's first reaction. It does not establish false-positive
performance or prove accident prevention. No video-based ground-truth validation.

Validation: six detector tests (including braking, adequate deceleration, bad data,
cut-ins, urgent warning, cooldown/rearm), 17 Hub tests, Ruff, and native isolated
integration on the comma verifying banner/chime with brake pressed, VisualAlert.none
and urgent FCW precedence. The staged integration files live only in /tmp; they did
not replace running selfdrived. Wider local alert test collection was unavailable
because the local environment lacks zstandard. No claim of complete stack replay.

## Longitudinal behavior and prior research

On this non-ACC port, openpilot steers but does not accelerate, brake, or maintain
a following gap. Ford's normal cruise controls remain independent; brake input
does not disengage this custom steering mode. Panda uses lateral-only/main-switch
safety permissions. A calculated longitudinal plan or displayed lead does not
mean openpilot will actuate it. The existing model FCW path suppresses on brake
press; the planner FCW path is separate. This change preserves both paths.

Low-speed steering research is documented in [the research record](BRONCO_LOW_SPEED_RESEARCH.md).
Its shadow commands remain unsent; no stronger steering is enabled by this warning.

## Delivery and rollback

This change is published to the public fork and documented privately. Publishing
does not install or activate it on the comma; the device remains on its previously
installed steering/Hub revision plus the separately installed shadow recorder.
No onroad process was restarted for this feature. A future deployment requires a
parked/offroad update and native startup verification.

Once installed, disable the advisory via the Hub while ignition is off. This does
not disable urgent FCW. To remove the implementation, revert its dedicated feature
commit after reviewing later changes; keep shadow-recording fixes intact. No Panda
firmware rollback is required. Private route data and credentials are not included
in the public repository.
