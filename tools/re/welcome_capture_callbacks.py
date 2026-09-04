"""Minimal one-boot Welcome capture and watchdog-diagnosis composition.

The broader setup-path trace established every launch decision already.  This
composition keeps only the behavior needed to reach and capture pixels: BKS
check-in, the two precisely attributed SpringBoard exception boundaries,
RunningBoard termination attribution, and the IOSurface/IOMFB handoff.  The
smaller breakpoint set also reduces debugger wall-clock overhead while iOS's
foreground scene-create watchdog is armed.
"""
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bks_checkin_callbacks
import first_surface_callbacks
import frontboard_watchdog_callbacks
import process_termination_callbacks
import welcome_abort_callbacks


def install(debugger, slide):
    for name in ("bks_checkin_callbacks", "first_surface_callbacks",
                 "frontboard_watchdog_callbacks",
                 "process_termination_callbacks", "welcome_abort_callbacks"):
        debugger.HandleCommand(
            "command script import %s" % os.path.join(HERE, name + ".py"))
    bks_checkin_callbacks.install(debugger, slide)
    first_surface_callbacks.install(debugger, slide)
    welcome_abort_callbacks.install(debugger, slide)
    frontboard_watchdog_callbacks.install(debugger, slide)
    process_termination_callbacks.install(debugger, slide)
    print("WELCOME_CAPTURE_TRACE_READY slide=0x%x" % int(slide), flush=True)
