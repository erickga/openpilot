from opendbc.car.ford.values import FordFlags


def should_cancel_cruise(CP, cruise_enabled: bool, openpilot_enabled: bool) -> bool:
  ford_main_switch = CP.brand == 'ford' and bool(CP.flags & FordFlags.MAIN_SWITCH)
  return cruise_enabled and (not openpilot_enabled or not CP.pcmCruise) and not ford_main_switch
