"""Existing display allocation/timing accommodations, without touch polling."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'re'))
import frontboard_watchdog_callbacks
import surface_cache_probe


def install(debugger, slide):
    debugger.HandleCommand('settings set target.process.disable-memory-cache true')
    for module in (frontboard_watchdog_callbacks, surface_cache_probe):
        debugger.HandleCommand('command script import "'+module.__file__+'"')
    frontboard_watchdog_callbacks.EXTEND = True
    frontboard_watchdog_callbacks.install(debugger, slide)
    surface_cache_probe.ENABLE = True
    surface_cache_probe.install(debugger, slide)
