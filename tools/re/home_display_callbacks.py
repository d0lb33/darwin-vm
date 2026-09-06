"""Explicit 24A5430a Home-checkpoint diagnostic resume configuration.

Import after connecting LLDB to the paused restored VM, call install(debugger),
then continue. This preserves the experiment's software-surface allocation and
UI-gate overrides; it is not a normal activation/authentication implementation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import welcome_abort_callbacks
import frontboard_watchdog_callbacks
import surface_cache_probe
import home_startup_probe


def install(debugger, slide=0x14f94000):
    # LLDB resolves callback names in its script namespace, not sys.modules.
    # Register each module before SetScriptCallbackFunction is invoked.
    for module in (welcome_abort_callbacks, frontboard_watchdog_callbacks,
                   surface_cache_probe, home_startup_probe):
        debugger.HandleCommand('command script import "' + module.__file__ + '"')
    debugger.HandleCommand('settings set target.process.disable-memory-cache true')
    welcome_abort_callbacks.install(debugger, slide)
    frontboard_watchdog_callbacks.EXTEND = True
    frontboard_watchdog_callbacks.install(debugger, slide)
    surface_cache_probe.ENABLE = True
    surface_cache_probe.install(debugger, slide)
    home_startup_probe.install(debugger, slide)
    home_startup_probe.install_ui_auth_diagnostic(debugger, slide)
