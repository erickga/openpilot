# Bronco low-speed transition research — 2026-11-01

## Status and recovery checkpoint

This is an **offline eligibility prototype**, not a working path-angle controller or
a vehicle release. Nothing is imported by the driving stack, no CAN messages are
generated, and no Panda limits or engagement behavior have changed. No device
deployment, public push, or private push was performed for this experiment.

Starting revision: `cb7202ba3d90fa7c8c8d715fdaecc3fa76d24268`.
Starting branch: `bronco-sport-v0.11.2` (clean before work).
Research branch: `research/bronco-low-speed-transition`.

The user requested extra assistance only below 30 km/h near the existing 0.020/m
curvature limit, with documented rollback. The agreed first stage was offline
development before controlled validation. The later request to review BluePilot
notes uncovered additional reasons not to implement an uncalibrated actuator blend.

## What was implemented

- `tools/car_porting/ford_low_speed_research.py`: CSV replay of a proposed
  eligibility scheduler. Its dimensionless weight is not a steering command.
- `tools/car_porting/ford_eps_status_audit.py`: raw received CAN 972 status audit.
  Bus 0 only; excludes returned TX and forwarded copies. Decodes availability,
  actuation denied and hands-off status using the local Ford DBC.
- `tools/car_porting/tests/test_ford_low_speed_research.py`: dwell, slew, speed
  envelope, hysteresis, sign reversal, invalid data, gaps, driver override and
  fault/disengagement tests.

Experimental scheduler: demand >=0.018/m for 0.25 seconds latches eligibility;
demand <=0.016/m releases it. Candidate weight fades in between 10–15 km/h,
is fully speed-eligible at 15–25, and fades out at 25–30. Weight is zero at or
above 30 and at or below 10. Normal weight slew is at most 1/s. Turn signals are
a conservative lane-change exclusion in replay. Driver pressure, faults,
disengagement, invalid values and timestamp discontinuities clear the state.
Opposite-direction requests require a new dwell. These constants are research
choices, not established Ford safety limits.

**Known unresolved tradeoff:** hard exclusions and a rapidly falling speed envelope
can override the normal slew bound. An arbitrary sudden speed jump cannot guarantee
both instantaneous zero assistance at 30 and continuous assistance. This scheduler
exposes that problem; it does not solve physical actuator handover. Its weight must
not be multiplied into live CAN fields. Curvature and path angle are not equivalent
units, and linear interpolation of the fields is not proven to interpolate response.

## BluePilot evidence and context

Pinned source: [bp-7.0, 773f7f1dc27cd10d622576a5074de4fefe60da73](https://github.com/BluePilotDev/bluepilot/tree/773f7f1dc27cd10d622576a5074de4fefe60da73).
Reviewed Ford controller/extension code and inline comments, Panda checks/tests,
README release/feature notes, RELEASES.md, and public PR descriptions/discussions.
RELEASES.md largely records upstream releases, not detailed Ford validation.

1. `carcontroller.py` selects curvature-primary or angle-primary controllers.
   Angle-primary uses path angle with curvature held at zero; it is not merely
   overflow added above 0.020. Bronco geometry is present but that is not validation
   of this custom non-ACC firmware variant. Existing comments describing a simple
   lookahead polynomial must not be treated as a verified EPS transfer function.
2. README advertises independent safety enforcement. However, the pinned
   `safety/modes/ford.h` lines 699–708 clear accumulated violations when curvature
   and path angle are zero and for 60 subsequent nonzero frames. Tests explicitly
   drain this latch before certain checks. Do not reuse that bypass. This is a
   source inspection finding; the complete BluePilot safety suite was not run.
3. [PR #148](https://github.com/BluePilotDev/bluepilot/pull/148) reports unwanted
   steering-release pulses during genuine curve-entry lag, followed by driver
   intervention. Its author reports improving the detector using fractional
   delivery. This is contributor evidence, not independently reproduced here.
4. [PR #153](https://github.com/BluePilotDev/bluepilot/pull/153) describes extending
   rescue pulses to lower speeds with pinion measurement, but explicitly distinguishes
   post-driver-override attenuation from speed-dependent EPS authority reduction.
   It warns the exact revision was not road validated and a pulse cannot fix all
   low-speed deficits. We are not importing reset pulses.
5. [PR #155](https://github.com/BluePilotDev/bluepilot/pull/155) reports a roughly
   40 km/h availability boundary on examined Q3 vehicles and introduces telemetry.
   We independently decoded the existing Bronco recordings below. The checked
   discussion contains a rebase request, not additional vehicle validation; #148
   and #153 returned no discussion/review comments through the connector.
6. [PR #216](https://github.com/BluePilotDev/bluepilot/pull/216) reports NaN lane
   confidence poisoning the angle controller. This motivates explicit finiteness
   tests; lane-centering trim is not included in this experiment.
7. [PR #220](https://github.com/BluePilotDev/bluepilot/pull/220) describes an
   Expedition-specific gain as a placeholder. Platform defaults cannot be assumed
   calibrated on every Ford. No Expedition gain is used here.

The actual angle-primary implementation includes prediction, tunable gains,
driver intervention logic, rate limits and PSCM-specific recovery behavior. A
selectable strategy is not evidence of seamless threshold-based handover. The
proposed automatic transition remains our own unvalidated research design.

## Recorded-data findings and verification

Four previously extracted full-log segments were replayed, in sorted filename order:

| Segment ordinal | Input samples | Candidate-weight samples | Maximum weight |
| --- | ---: | ---: | ---: |
| 1 | 5622 | 1162 | 1.000 |
| 2 | 6000 | 1517 | 1.000 |
| 3 | 6000 | 339 | 0.740 |
| 4 | 6000 | 106 | 0.517 |

Counts are sample counts, not durations or successful turns. Replay uses recorded
controller requests and state; it is open-loop and cannot predict a changed
vehicle trajectory. No command conversion or improved turn performance is claimed.

EPS audit: 8042 raw bus-0 frames; all availability=1 and actuation-denied=0.
Availability=1 means LCA/LKA suppressed, LDW available in the local DBC. Of these,
7560 had a preceding carState <=150 ms old and speed below 30 km/h; 273 were in
30–40 km/h; 209 had no fresh preceding speed. No >=40 km/h frames were classified
in these selected segments, so they do not establish a 40 km/h threshold on this
Bronco. Existing steering still operated: this status is not proof that the EPS
rejects every request or proof of a quantitative authority ceiling.

Private raw routes, identifiers and per-sample CSV outputs remain outside this
repository under the existing local analysis directory; no credentials are added.
Local artifacts: `transition-study-results.json`, `*-full-transition.csv`, and
`eps-status-audit.json` in `/tmp/bronco-turn-analysis-20261101`. Temporary storage
is not a durable private archive; preserve that directory privately if needed.

Reproduce scheduler tests (standard library only):

```sh
python3 -m unittest tools.car_porting.tests.test_ford_low_speed_research -v
```

Replay an extracted CSV without overwriting it:

```sh
python3 tools/car_porting/ford_low_speed_research.py INPUT-full.csv --output OUTPUT-transition.csv
```

Audit raw logs using the repository's capnp-capable Python environment and zstd:

```sh
PYTHONPATH=.:opendbc_repo python tools/car_porting/ford_eps_status_audit.py PATH/TO/rlog.zst
```

Validation: six scheduler tests pass; Ruff checks pass after correcting an explicit
zip strictness flag. No on-device tests, complete Panda suite, BluePilot suite,
closed-loop simulation, or road tests were performed for this research change.

## Gates before any actuator implementation/deployment

1. Establish vehicle-specific path-angle response and whether additional authority
   exists below 30; current curvature-only recordings cannot identify that mapping.
2. Design coordinated curvature/path-angle bounds and matched-response handover,
   including accelerating across 30, slowing below 10, override, sign reversals,
   stale data, restart and loss of engagement. A scheduler pass is not sufficient.
3. Independently enforce the accepted command combinations in Panda without bypass
   windows or trusting self-reported requested curvature as sole physical feedback.
   Preserve the non-ACC main-switch latch and all longitudinal prohibitions.
4. Add protocol, fault-injection, safety and regression tests, then shadow logging.
   Shadow mode must not change transmitted commands. No automatic reset pulses,
   lane offset changes or suppression of saturation alerts are planned.
5. Controlled vehicle validation is necessary to assess feel and authority. No
   public-road readiness or seamless transition is claimed by this branch.

## Rollback

There is **no vehicle rollback needed**: the comma and its Panda firmware were not
changed. Do not reinstall software or restore an old device backup for this offline
experiment; an older backup could also undo unrelated Hub fixes.

After committing this research work, return the local checkout to the original
branch with:

```sh
git status --short
git switch bronco-sport-v0.11.2
git rev-parse HEAD
```

Verify HEAD equals the starting revision above. Preserve any later uncommitted work
before switching; do not use reset --hard or clean. The research branch retains the
experiment for review. If this research commit is later merged, use `git revert`
on that exact commit after reviewing subsequent changes. A future vehicle release
must have its own recorded software AND matching Panda firmware rollback procedure;
this document does not authorize deployment or firmware changes.
