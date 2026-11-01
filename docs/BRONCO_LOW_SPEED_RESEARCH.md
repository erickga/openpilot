# Bronco low-speed transition research — 2026-11-01

## First multi-drive review — snapshot 2026-11-01 23:38 UTC

Copied the five retained session directories privately for offline analysis. Three
contain movement above 3 km/h; two are stationary. The active stationary JSONL
changed during tar collection, so tar returned a warning; the archive extracted
and all 88,888 sample records decoded successfully, with no malformed lines.
Statistics describe this snapshot, not the ongoing end of the current session.

Durations sum adjacent monotonic sample intervals only when 0 < dt <=150 ms;
there were no larger gaps within the sampled sessions. ~83.34 minutes recorded,
31.62 minutes moving, 37.20 minutes lateral-active (includes standstill), and
12.62 minutes valid lateral-active between 10 and 30 km/h. Initial/stale invalid
data totaled 19.32 seconds. Stationary requested-curvature values are not meaningful
turn demand and were excluded by the existing gates.

| Moving session (review order) | Moving minutes | Low-speed command at cap, seconds | Shadow eligible, seconds |
| --- | ---: | ---: | ---: |
| A | 7.55 | 53.36 | 11.30 |
| B | 10.24 | 31.03 | 5.58 |
| C | 13.82 | 46.87 | 18.26 |

The cap criterion was abs(actual controller output)>=0.0199/m, lateral active,
valid/no fault, and speed strictly between 10 and 30 km/h. Total cap exposure was
131.27 seconds; 35.59 seconds had no driverPressed flag. That flag's absence does
not establish hands-off driving: driver effort below its threshold and prior input
can influence measured yaw. Do not use yaw/command ratios as isolated EPS gain.

Shadow weight was positive for 35.14 seconds, including 23 contiguous windows
of at least 0.5 seconds. None occurred at >=30 km/h or with driverPressed=true.
One 2.97-second window at 19.18–22.08 km/h reached requested curvature 0.030396/m,
full candidate weight and the experimental 0.15-rad angle cap. Across all sessions,
the weighted hypothetical angle reached that cap for 0.97 seconds.

**Reject the current transition implementation for actuation:** nine exits from
|shadow angle|>0.01 rad went directly to zero on driver/turn-signal exclusion. Two
went from |0.15| rad to zero over 50–60 ms while the proposed curvature field
returned from zero to |0.020|/m. This is a discontinuity in the candidate command
representation, not a measured steering jerk (the commands were never sent).
Driver disengagement must remain prompt; a revised design must distinguish
disabling assistance from handing control back between active actuators, rather
than simply smoothing every safety exit indiscriminately.

77,011 sample rows contained fresh (<150 ms old) actual outgoing 979 snapshots;
all decoded actual path angles were zero. These snapshots may repeat frames and
are not an exhaustive wire capture. They corroborate unchanged actual actuation.

EPS availability was mostly 1 below 40 km/h and 3 above 40 in this sample, with
brief boundary overlap. This is independent observational support for a speed
boundary in this status on this vehicle, but it still does not establish TJA
authority or path-angle response. No actuation-denial conclusion is inferred.

Session names from two boots reflect an incorrect initial wall clock (the filenames
start with an older timestamp despite later recordings). Use monotonic timestamps
within sessions; do not assign calendar times from filenames or concatenate their
monotonic clocks across boots. The current filename-based oldest-first pruning can
also misorder those sessions once the retention threshold is reached; only five
exist now, so count-based deletion has not yet been required. This should be fixed
before relying on chronological ten-drive retention.

Private snapshot, per-session summaries, transition windows and reproducible
analysis scripts: `/tmp/bronco-shadow-review-20261101/` (`summary.json`,
`transitions.json`, `analyze.py`, `transitions.py`). No raw logs/route identifiers
were added to the repository. No device behavior was changed during this review.
Next controller work should address handover discontinuities in shadow mode;
these data do not validate the untransmitted curvature-to-path-angle mapping.

## Automatic multi-drive collection — supersedes one-hour launch

User subsequently requested collection across drives, retaining five to ten and
overwriting old recordings. Configured a maximum of **10 drive sessions**, with
512 MiB total retention and the existing ~64 MiB per-session rolling limit.
Pruning runs at session transitions and every 30 seconds; the total cap can be
briefly exceeded between checks. Older drives may be deleted before reaching ten
when the byte cap is reached. Each unusually long drive retains its newest tail.
The original one-hour logs outside `drives/` are preserved separately, not pruned.

`ford_shadow_service.py` listens only to deviceState and starts/stops the existing
observer on fresh, valid onroad/offroad state. A state older than five seconds stops
collection; recovery starts a new session. Sessions are therefore ignition/onroad
windows, not guaranteed trips with movement. At most one supervisor owns a file
lock. Observer failures retry no more than every 30 seconds within the same session.
No changes are made to CAN transmission, steering controllers or Panda.

Persistence is one PythonProcess registration in
`openpilot/system/manager/process_config.py`, named `ford_shadow`, using `only_onroad`.
This adds an optional logging child, not a controller hook. A systemd installation
was attempted but `/etc` is read-only on AGNOS; no system unit was installed and
the unused template was removed. The original manager config was verified against
the stable commit and backed up to `/data/bronco-shadow/backup/process_config.py`
(SHA-256 `c240ecabc06c1f38020aa4e63a97413c2def562ce134e028ecc31516d79cc074`).

Deployment verified at approximately 2026-11-01 20:06 UTC: old observer stopped,
new supervisor/observer running, first `/data/bronco-shadow/drives/drive-*/shadow.jsonl`
growing, no supervisor console errors. Correct manager module imports and onroad
predicate True/offroad False verified; 16 local tests and Ruff pass. Car process
PID remained unchanged. A full reboot/physical ignition-cycle test was not performed.
The current supervisor was launched separately to avoid restarting openpilot;
future manager startups use the registered child automatically. Software updates
that replace process_config may remove this local registration.

Disable/rollback: remove only the `ford_shadow` registration line (or restore the
backed-up config ONLY if no later changes need preserving), and stop the verified
`tools.car_porting.ford_shadow_service` PID with SIGTERM. Its finally block stops
its observer. If manager already owns the supervisor, edit the registration first
and apply it at the next safe manager restart; do not restart the driving stack
while driving. Inspect processes to confirm both are stopped. Keep recorded logs;
the added tools can remain inert. No Panda rollback is involved.

Files to preserve privately for analysis: the complete `drives/` directory, plus
matching standard rlogs. No remote upload or public/private repository push was
performed as part of enabling collection.

## Shadow-only installation verified 2026-11-01 20:00 UTC

At the user's explicit request, copied ONLY `ford_shadow_logger.py` and
`ford_low_speed_research.py` from research revision `9fb5c57d6` into the existing
`/data/openpilot/tools/car_porting/` directory. Neither destination file existed
before installation. Device HEAD remains `cb7202ba3d90fa7c8c8d715fdaecc3fa76d24268`;
no checkout, rebuild, firmware flash, or driving-process restart was performed.
Pre-existing generated/native build differences were left untouched. Ford controller
and safety source diffs remain empty.

The working device interpreter is `/usr/local/venv/bin/python`; `/usr/bin/python3`
lacks capnp. Started the observer detached with that virtualenv, repository-root
working directory, `PYTHONPATH=.:opendbc_repo`, duration 3600 seconds, output directory
`/data/bronco-shadow`, and console output `observer.out`. Initial observer PID 5449.
Use process discovery rather than assuming this PID remains valid in future sessions.

Validated fresh carState/carControl/carOutput, supported-platform=true, actual CAN
126/972/979 and Panda returned 979 snapshots. At standstill with steering inactive,
shadow weight and both proposed fields were zero. 285 JSONL records existed at the
verification checkpoint. No console errors. Initial sampled memory was ~37 MiB;
process nice level 10. This verifies native IPC and file logging on the device,
not controller performance or a completed moving-drive collection.

SHA-256 matched locally and remotely:

- logger: `30ad27d68edb8d172fe4ad756d9de57f53eebf2561677d9c585309ddb39dc134`
- scheduler: `b7eadad0ca1e5e9fb37361e5bc97ec36ffaea26e39c3e7e297dfb026e9a2d1ac`

Stop/rollback: inspect `pgrep -af tools.car_porting.ford_shadow_logger`, then send
SIGINT to the verified Python observer PID (`kill -INT <pid>`). Do not terminate
card/manager or restore old firmware. The process also expires after one hour and
does not restart on boot. Preserve `/data/bronco-shadow` logs. To remove the feature,
after stopping it remove only the two added Python files after verifying their
hashes still match; they had no predecessors to restore. No production branch
rollback is necessary. Earlier sections below describe pre-installation status.

## Receive-only shadow logger added after the initial study

`tools/car_porting/ford_shadow_logger.py` is an optional standalone observer. It
subscribes to carState/carControl/carOutput/carParams and receives CAN/sendcan;
it creates no publisher, CAN packer, Panda connection, or driving-process hook.
It is NOT enabled automatically and has not been installed or run on the comma.
The original offline-study status below describes the earlier checkpoint.

The observer records, at up to 20 Hz:

- Proposed transition weight/reason plus hypothetical curvature and path-angle
  fields, both internal and wire-sign representations, explicitly marked unsent.
- Actual actuator output, yaw rate/derived curvature, steering angle, driver
  torque, speed, override, engagement, turn signals, CAN validity and steering faults.
- Source monotonic timestamps and age for alignment; stale/invalid inputs disable
  the candidate. Candidate generation is limited to non-ACC, non-CANFD Bronco Sport.
- Latest sampled raw CAN 126 (pinion), 972 (EPS status), and 979 (actual steering),
  including sendcan, bus-0 RX and Panda returned/rejected copies when observed.
  Each retains its timestamp and hex bytes; 979 is additionally decoded. Pinion
  bytes need subsequent DBC/quality/checksum validation before treating them as a
  measurement. Conflated subscriptions mean these are snapshots, NOT a complete
  CAN capture. A retained snapshot can be old: use its timestamp, not row time.

The hypothetical angle uses requested curvature * speed * 1.0 second, capped at
0.15 rad, weighted by the scheduler; hypothetical curvature fades from actual
output to zero. This is a BluePilot-inspired research hypothesis, not a port of
its controller or a calibrated actuator conversion. Wire-angle negation follows
the inspected BluePilot implementation as a hypothesis; the conflicting semantic
sign comments remain unresolved. No safety certification is implied by the cap.
There is no automatic reset pulse. Hard gates/sign reversals can be discontinuous;
these are findings for review, not actuator-ready transitions. Inactive rows may
retain the recorded actual curvature as the baseline while shadow angle is zero.

**Causal limitation:** real yaw/pinion response is to the real STOCK commands.
These logs can reject poor transition scheduling and implausible command sequences;
they cannot identify the physical response to an untransmitted path-angle command.

Files are owner-only, rotating JSONL: `shadow.jsonl` plus seven backups, roughly
64 MiB total across sessions. Oldest files are overwritten. A single-writer lock
prevents conflicting runs. Each session includes wall/monotonic anchors and hashes
of both experiment source files. Copy logs privately before rotation loses the
desired drive. No VIN, GPS or Hub key is collected by this observer. Normal rlogs
are still needed for full CAN and Panda-state analysis. Disk errors stop the
observer only; no automatic restart is registered. It runs at reduced priority.

After a separately reviewed installation, start from the repository root using
the device's configured Python environment (the module must be importable):

```sh
PYTHONPATH=.:opendbc_repo python -m tools.car_porting.ford_shadow_logger \
  --output-dir /data/bronco-shadow --duration 3600
```

Keep the terminal session open. Ctrl-C stops it; otherwise it exits after one hour.
Starting/stopping requires no driving-process restart or firmware flash. Rollback
is simply stopping the observer; reverting its commit removes the optional code.
Do not delete collected logs as part of rollback. No Hub switch or automatic launch
was added. This command is documented, not executed on the vehicle in this task.

Validation: 13 standard-library tests cover candidate generation, exclusions,
sign symmetry, raw-command decoding, nonfinite values, bounded file rotation,
permissions and writer locking, plus the original scheduler behavior. Local native
IPC smoke testing is unavailable because this Python environment lacks `msgq`;
live subscriptions and on-device resource use remain to be verified before logging
a drive. Production steering and Panda source files are unchanged.

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

### Correction after follow-up review

The availability observation must not be used to infer that additional path-angle
authority is limited. `fordcan.py` distinguishes the Lane_Assist_Data1 LKA interface
from LateralMotionControl TJA/LCA and explicitly describes the latter as not subject
to the PSCM lockout. Ford's owner documentation distinguishes stock lane keeping
(above 40 mph / 64 km/h) from lane centering. That user-facing threshold does not
establish the precise scope of CAN 972's availability signal on this configured
EPS. Neither the inference of a TJA authority reduction nor the opposite assertion
that the signal applies exclusively to LKA has been established here. Denied=0
and observed curvature response demonstrate operation, not unused authority or a
validated path-angle mapping. The previous user-facing implication of limited
path-angle benefit from this status alone was too strong and is withdrawn.

The proposed anti-overshoot change also needs qualification: at 19.7 km/h the
current interpolation is approximately 90.6% unfiltered / 9.4% filtered, not a
full five-second lag. Moving the blend to 11–16 m/s would alter steering up to
57.6 km/h, contrary to the agreed below-30 scope. The ramp-type comment explicitly
says it makes no difference with curvature control. A residual-curvature times
lookahead formula is a geometric hypothesis, not a verified combined EPS response;
the documented signs of path angle and curvature differ. A same-sign gate plus
an absolute angle cap is not a complete independent safety design.

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
