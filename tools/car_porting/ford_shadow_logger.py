"""Receive-only Ford transition experiment. No publisher, CAN packer or device access.

Hypothetical actuator fields are uncalibrated numerical proposals, never sent.
Actual response belongs to the STOCK command, not to the shadow proposal.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import time

from tools.car_porting.ford_low_speed_research import Observation, TransitionStudy

VERSION = "ford-shadow-v1"


def decode_lmc(data: bytes) -> dict:
  """DBC wire units, deliberately distinct from controller-internal signs."""
  return {"curvature_wire": (((data[0] << 3) | (data[1] >> 5))-1000)*.00002,
          "path_angle_wire_rad": (((data[3] << 3) | (data[4] >> 5))-1000)*.0005,
          "mode": (data[4] >> 2) & 7}


class ShadowModel:
  def __init__(self):
    self.study = TransitionStudy()

  def update(self, observation: Observation, actual_curvature: float) -> dict:
    if not math.isfinite(actual_curvature):
      observation = Observation(observation.time, observation.kph, observation.curvature,
                                observation.active, observation.driver_pressed, False)
    d = self.study.update(observation)
    # BluePilot-inspired kappa*v gain, intentionally NOT its complete controller.
    # 1.0 s gain and 0.15 rad cap are experiment parameters, not safety limits.
    # Wire sign is an explicit hypothesis matching BP's negation, NOT calibrated
    # from Ford's conflicting c1/c2 sign comments. Record both conventions.
    angle = max(-.15, min(.15, observation.curvature * observation.kph / 3.6)) if d.weight else 0.0
    stock = actual_curvature if math.isfinite(actual_curvature) else None
    curvature = (1.0-d.weight)*stock if stock is not None else None
    return {"decision": asdict(d), "shadow_curvature_internal": curvature,
            "shadow_path_angle_internal_rad": d.weight*angle,
            "shadow_curvature_wire": -curvature if curvature is not None else None,
            "shadow_path_angle_wire_rad": -d.weight*angle,
            "mapping_validated": False, "transmitted": False}


def clean(value):
  if isinstance(value, float) and not math.isfinite(value):
    return None
  if isinstance(value, dict):
    return {k: clean(v) for k, v in value.items()}
  if isinstance(value, list):
    return [clean(v) for v in value]
  return value


class Journal:
  """Bounded, owner-only logs. Disk failures stop ONLY this observer.

  A fixed file set limits storage across runs, not just per invocation. One writer
  lock prevents two observers rotating the same files. No raw location or VIN.
  """
  def __init__(self, directory: Path, max_bytes=8*1024*1024, backups=7):
    import fcntl
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.lock = (directory / ".lock").open("w")
    try:
      fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
      self.lock.close()
      raise
    self.path = directory / "shadow.jsonl"
    self.max_bytes, self.backups = max_bytes, backups
    self.file = None
    self.write({"type": "session", "version": VERSION,
                "source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                  for name in ("ford_shadow_logger.py", "ford_low_speed_research.py")},
                "wall_time_ns": time.time_ns(),
                "monotonic_ns": time.monotonic_ns(), "pid": os.getpid(),
                "gain_seconds": 1.0, "angle_cap_rad": .15,
                "notice": "Unsent hypotheses; measured response is to stock commands"})

  def write(self, row):
    encoded = json.dumps(clean(row), allow_nan=False, separators=(",", ":")) + "\n"
    if self.path.exists() and self.path.stat().st_size + len(encoded.encode()) > self.max_bytes:
      if self.file:
        self.file.close()
        self.file = None
      for i in range(self.backups, 0, -1):
        src = self.path if i == 1 else Path(str(self.path)+f".{i-1}")
        if src.exists():
          src.replace(Path(str(self.path)+f".{i}"))
    if self.file is None:
      fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
      os.fchmod(fd, 0o600)
      self.file = os.fdopen(fd, "a", buffering=1)
    self.file.write(encoded)

  def close(self):
    if self.file:
      self.file.close()
    self.lock.close()


def run(directory: Path, duration: float):
  # Lazy import permits offline testing without IPC/native dependencies.
  import openpilot.cereal.messaging as messaging
  from opendbc.car.ford.values import CAR, FordFlags
  os.nice(10)
  services = ["carState", "carControl", "carOutput", "carParams"]
  sm = messaging.SubMaster(services, poll="carState")
  # These are receive-only subscriptions. Conflation bounds the observer's queue;
  # raw samples are snapshots, not a lossless substitute for standard rlogs.
  can = messaging.sub_sock("can", conflate=True)
  sendcan = messaging.sub_sock("sendcan", conflate=True)
  model = ShadowModel()
  journal = Journal(directory)
  start = time.monotonic()
  last = 0.0
  raw = {}
  try:
    while time.monotonic()-start < duration:
      sm.update(100)
      now = time.monotonic()
      for channel, sock in (("can", can), ("sendcan", sendcan)):
        event = messaging.recv_one_or_none(sock)
        if event is not None:
          for packet in getattr(event, channel):
            if packet.address in (126, 972, 979) and packet.src in (0, 128, 192) and len(packet.dat) == 8:
              raw[f"{channel}:{packet.src}:{packet.address}"] = {
                "mono_ns": event.logMonoTime, "hex": bytes(packet.dat).hex(),
                "lmc": decode_lmc(bytes(packet.dat)) if packet.address == 979 else None}
      if now-last < .05:
        continue
      last = now
      ages = {s: now-sm.logMonoTime[s]/1e9 if sm.seen[s] else None for s in services}
      valid = all(sm.seen[s] and sm.valid[s] and 0 <= ages[s] <= .15 for s in services[:3])
      cp = sm["carParams"]
      supported = (sm.seen["carParams"] and cp.carFingerprint == CAR.FORD_BRONCO_SPORT_MK1
                   and bool(cp.flags & FordFlags.NON_ACC) and not bool(cp.flags & FordFlags.CANFD))
      cs, cc, co = sm["carState"], sm["carControl"], sm["carOutput"]
      observation = Observation(now, cs.vEgoRaw*3.6, cc.actuators.curvature, cc.latActive,
                                cs.steeringPressed, valid and supported and cs.canValid,
                                cs.steerFaultTemporary or cs.steerFaultPermanent,
                                cs.leftBlinker or cs.rightBlinker)
      result = model.update(observation, co.actuatorsOutput.curvature)
      journal.write({"type": "sample", "version": VERSION, "mono_ns": int(now*1e9),
                     "source_mono_ns": dict(sm.logMonoTime), "ages_s": ages,
                     "supported_platform": supported, "observation": asdict(observation),
                     "actual": {"curvature_output": co.actuatorsOutput.curvature,
                                "yaw_rate": cs.yawRate, "steering_angle_deg": cs.steeringAngleDeg,
                                "driver_torque_nm": cs.steeringTorque, "v_ego_mps": cs.vEgo,
                                "yaw_curvature_internal": -cs.yawRate/max(cs.vEgoRaw, .1)},
                     "shadow": result, "raw_snapshots": raw})
  finally:
    journal.close()


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--output-dir", type=Path, required=True)
  parser.add_argument("--duration", type=float, default=3600, help="Maximum seconds; default one hour")
  args = parser.parse_args()
  if not math.isfinite(args.duration) or args.duration <= 0:
    parser.error("duration must be finite and positive")
  try:
    run(args.output_dir, args.duration)
  except KeyboardInterrupt:
    pass
