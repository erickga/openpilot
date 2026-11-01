"""Boot supervisor for receive-only shadow collection; no driving process hooks."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path("/data/bronco-shadow/drives")
MAX_DRIVES = 10
MAX_BYTES = 512 * 1024 * 1024


def write_json(path: Path, value):
  temporary = path.with_suffix(path.suffix + ".tmp")
  with temporary.open("w") as output:
    json.dump(value, output)
    output.write("\n")
    output.flush()
    os.fsync(output.fileno())
  temporary.replace(path)


def sequence(path: Path) -> int | None:
  try:
    value = json.loads((path / "session.json").read_text()).get("sequence")
    return value if type(value) is int and value > 0 else None
  except (OSError, ValueError):
    return None


def new_session(root: Path) -> Path:
  # Called while holding the supervisor lock. Wall time is metadata only.
  counter = root / "sequence.json"
  previous = json.loads(counter.read_text()) if counter.exists() else 0
  previous = max(previous, max((sequence(p) or 0 for p in root.glob("drive-*")), default=0))
  number = previous + 1
  write_json(counter, number)  # persist allocation before creating a directory
  active = root / f"drive-seq-{number:010d}"
  active.mkdir(mode=0o700)
  write_json(active / "session.json", {"sequence": number, "start_wall_ns": time.time_ns(),
                                      "start_mono_ns": time.monotonic_ns(), "shadow_only": True})
  return active


def prune(root: Path, active: Path | None = None):
  """Remove only our marked drive directories; preserve unrelated files."""
  import shutil
  # Unmigrated legacy sessions are protected, never ordered by unreliable clocks.
  drives = sorted((p for p in root.glob("drive-*") if p.is_dir() and not p.is_symlink()
                   and sequence(p) is not None), key=sequence)
  def size(path):
    return sum(p.stat().st_size for p in path.iterdir() if p.is_file() and not p.is_symlink())
  sizes = {p: size(p) for p in drives}
  while len(drives) > MAX_DRIVES or sum(sizes[p] for p in drives) > MAX_BYTES:
    victim = next((p for p in drives if p != active), None)
    if victim is None:
      break
    shutil.rmtree(victim)
    drives.remove(victim)


def is_driving(seen: bool, valid: bool, started: bool, age: float) -> bool:
  return seen and valid and started and 0 <= age <= 5.0


def stop(child):
  if child is not None and child.poll() is None:
    child.send_signal(signal.SIGINT)
    try:
      child.wait(timeout=5)
    except subprocess.TimeoutExpired:
      child.terminate()
      try:
        child.wait(timeout=2)
      except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def main():
  import fcntl
  import openpilot.cereal.messaging as messaging
  ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
  # Prevent a manually started supervisor and manager from duplicating sessions.
  lock = (ROOT / ".supervisor.lock").open("w")
  try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
  except OSError:
    lock.close()
    return
  sm = messaging.SubMaster(["deviceState"], poll="deviceState")
  child = None
  active = None
  next_attempt = 0.0
  last_prune = 0.0
  driving_last = False
  def terminate(_signum, _frame):
    raise KeyboardInterrupt
  signal.signal(signal.SIGTERM, terminate)
  try:
    while True:
      sm.update(1000)
      now = time.monotonic()
      driving = is_driving(sm.seen["deviceState"], sm.valid["deviceState"], sm["deviceState"].started,
                           now-sm.logMonoTime["deviceState"]/1e9)
      if not driving:
        stop(child)
        child = None
        active = None
      elif child is None or child.poll() is not None:
        if now >= next_attempt:
          if active is None:
            active = new_session(ROOT)
          child = subprocess.Popen([sys.executable, "-m", "tools.car_porting.ford_shadow_logger",
                                    "--output-dir", str(active), "--duration", "86400"],
                                   stdin=subprocess.DEVNULL)
          next_attempt = now + 30  # bounded retry if an observer fails
      if now-last_prune >= 30 or driving != driving_last:
        prune(ROOT, active)
        last_prune = now
      driving_last = driving
  finally:
    stop(child)
    lock.close()


if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    pass
