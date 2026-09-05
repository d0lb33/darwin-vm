"""Read-only 24A5430a D594/power-lock cycle witness; run stopped at EL1.

Caller supplies a link-state object, the waiting SwapEnd thread, and the
IORecursiveLock independently identified from its set_device_power stack.
No RAM scans, lock writes, native calls, or breakpoint installation.
"""
import json
import time
from pathlib import Path

from live_task_threads import _read


def capture(debugger, link, waiter, power_lock, path):
    process = debugger.GetSelectedTarget().GetProcess()

    def word(address, size=8):
        return int.from_bytes(_read(process, address, size), 'little')

    def thread_wait(address):
        if word(address + 0x1a0) != 0x2010002030100000:
            raise RuntimeError('invalid thread signature: %#x' % address)
        return word(address + 0x18)

    wait = thread_wait(waiter)
    slots = []
    for tag in range(4):
        slot = link + 0x408 + tag * 0x68
        owner = word(slot + 0x38)
        state = word(slot + 0x60, 4)
        slots.append(dict(tag=tag, slot=slot, state=state, owner=owner,
                          owner_wait=thread_wait(owner) if state == 1 and owner else 0))
    lock_owner = word(power_lock + 0x18)
    depth = word(power_lock + 0x20, 4)
    cycle = any(s['state'] == 1 and s['owner'] != waiter and
                wait == s['slot'] + 0x60 and
                s['owner_wait'] == power_lock + 8 for s in slots)
    result = dict(time=time.time(), link=link, waiter=waiter, waiter_wait=wait,
                  power_lock=power_lock, lock_owner=lock_owner, lock_depth=depth,
                  slots=slots, cycle=cycle and lock_owner == waiter and depth > 0)
    Path(path).write_text(json.dumps(result, indent=2) + '\n')
    print('DISPLAY_LOCK_CYCLE ' + json.dumps(result), flush=True)
    return result
