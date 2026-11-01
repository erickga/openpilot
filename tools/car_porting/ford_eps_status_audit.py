"""Read-only raw-CAN audit of Ford EPS availability; accepts local rlog.zst files.

Uses DBC 972 byte 0: bit7 hands off, bit6 denied, bits5:4 availability.
Only bus 0 RX frames count; returned TX and forwarded copies are excluded.
Counts are samples, not elapsed time. This is telemetry, not proof of ECU authority.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

from openpilot.cereal import log


def audit(path: Path) -> dict:
  counts = Counter()
  speed = None
  speed_time = None
  raw = subprocess.check_output(["zstd", "-q", "-d", "-c", str(path)])
  for event in log.Event.read_multiple_bytes(raw):
    timestamp = event.logMonoTime / 1e9
    if event.which() == "carState":
      speed, speed_time = event.carState.vEgo * 3.6, timestamp
    elif event.which() == "can":
      for packet in event.can:
        if packet.address != 972 or packet.src != 0 or len(packet.dat) != 8:
          continue
        fresh = speed_time is not None and 0 <= timestamp - speed_time <= .15
        band = "unknown" if not fresh else "below30" if speed < 30 else "30to40" if speed < 40 else "40plus"
        status = packet.dat[0]
        counts[(band, (status >> 4) & 3, (status >> 6) & 1, (status >> 7) & 1)] += 1
  return {"frames": sum(counts.values()), "groups": [
    {"speed_band": key[0], "availability": key[1], "denied": key[2], "hands_off": key[3], "frames": n}
    for key, n in sorted(counts.items())]}


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("rlogs", nargs="+", type=Path)
  args = parser.parse_args()
  print(json.dumps([audit(path) for path in args.rlogs], indent=2))
