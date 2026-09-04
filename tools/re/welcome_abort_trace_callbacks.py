"""One-boot composition for Setup lookup, first surface, and fatal abort."""
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import welcome_fast_callbacks
import welcome_abort_callbacks


def install(debugger, slide):
    # LLDB resolves Python callback names through command-script imports, not
    # ordinary Python imports alone.
    debugger.HandleCommand(
        "command script import %s" % os.path.join(
            HERE, "welcome_fast_callbacks.py"))
    debugger.HandleCommand(
        "command script import %s" % os.path.join(
            HERE, "welcome_abort_callbacks.py"))
    welcome_fast_callbacks.install(debugger, slide)
    welcome_abort_callbacks.install(debugger, slide)
    print("WELCOME_ABORT_TRACE_READY slide=0x%x" % int(slide), flush=True)
