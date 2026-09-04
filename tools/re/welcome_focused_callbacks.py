"""Low-overhead SpringBoard Welcome-path trace for initialized NVMe guests."""
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bks_checkin_callbacks
import sb_setup_path_callbacks
import welcome_abort_callbacks


def install(debugger, slide):
    for name in ("bks_checkin_callbacks", "sb_setup_path_callbacks",
                 "welcome_abort_callbacks"):
        debugger.HandleCommand(
            "command script import %s" % os.path.join(HERE, name + ".py"))
    bks_checkin_callbacks.install(debugger, slide)
    sb_setup_path_callbacks.install(debugger, slide)
    welcome_abort_callbacks.install(debugger, slide)
    print("WELCOME_FOCUSED_TRACE_READY slide=0x%x" % int(slide), flush=True)
