#!/usr/bin/env python3
"""Retired: the old installer bypassed driver disengagement protections.

Use a reviewed checkout and the normal build process. This script intentionally
neither patches files, builds/flashes firmware, nor creates a prebuilt marker.
"""
import sys

if __name__ == "__main__":
  sys.exit("Installer retired: do not apply the old Always-On Lateral patch. See BRONCO_SPORT_NON_ACC_SETUP.md.")
