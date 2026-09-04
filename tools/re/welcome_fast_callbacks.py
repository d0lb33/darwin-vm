"""Compose the narrow Welcome-path probes for one reusable guest boot.

This intentionally combines only three established observers: the
BackBoardServices check-in boundary, SpringBoard's Setup decision/activation
edges, and the first QuartzCore IOSurface handoff.  The condition watcher may
freeze at any selected success label; attach again and continue the same guest
instead of rebooting for the next layer.
"""
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bks_checkin_callbacks
import first_surface_callbacks
import sb_setup_path_callbacks


def install(debugger, slide):
    # Importing a Python module is not enough for LLDB's `breakpoint command
    # add -F module.callback` resolver.  Register each callback module with the
    # command interpreter before it creates its breakpoints.
    for name in ("bks_checkin_callbacks", "sb_setup_path_callbacks",
                 "first_surface_callbacks"):
        debugger.HandleCommand(
            "command script import %s" % os.path.join(HERE, name + ".py"))
    bks_checkin_callbacks.install(debugger, slide)
    sb_setup_path_callbacks.install(debugger, slide)
    first_surface_callbacks.install(debugger, slide)
    print("WELCOME_FAST_TRACE_READY slide=0x%x" % int(slide))
