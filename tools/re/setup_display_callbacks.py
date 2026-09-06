"""Resume the pre-completion Welcome checkpoint with display and touch.

Keeps the measured software-surface allocation diagnostic and watchdog grace.
Does not install Home's Setup/activation/authentication predicate overrides.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import welcome_abort_callbacks
import frontboard_watchdog_callbacks
import surface_cache_probe
import touch_bridge


def install(debugger, events_path, slide=0x14f94000):
    for module in (welcome_abort_callbacks, frontboard_watchdog_callbacks,
                   surface_cache_probe, touch_bridge):
        debugger.HandleCommand('command script import "' + module.__file__ + '"')
    debugger.HandleCommand('settings set target.process.disable-memory-cache true')
    welcome_abort_callbacks.install(debugger, slide)
    frontboard_watchdog_callbacks.EXTEND = True
    frontboard_watchdog_callbacks.install(debugger, slide)
    surface_cache_probe.ENABLE = True
    surface_cache_probe.install(debugger, slide)
    touch_bridge.install(debugger, events_path, slide)
