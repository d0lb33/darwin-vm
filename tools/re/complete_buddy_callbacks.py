"""Complete Buddy/Setup on a running SpringBoard by persisting the purplebuddy
completion preferences DEVICE-WIDE from inside the guest.

Setup Assistant never launches on these images, so its markBuddyComplete cannot
run (docs/re/setup-activation-contract.md). A first attempt wrote the keys with
CFPreferencesSetAppValue (current user = mobile); BYSetupAssistantNeedsToRun
still returned 1 for bluetoothd/CommCenter, which read a different user's
domain. This version writes the com.apple.purplebuddy keys to the **AnyUser /
AnyHost** system-wide domain (/Library/Preferences) with CFPreferencesSetValue,
so every process sees them, then CFPreferencesSynchronize flushes to the
persistent Data volume.

Keys set true: BYBuddyFinishedInitialRunKey, SetupFinishedAllSteps, SetupDone.
All registers saved/restored; guest resumes at the original PC. Debugger-driven
completion, verified by a fresh boot; not a default image change.
"""
import lldb
import welcome_abort_callbacks as names

SLIDE = 0

# CoreFoundation, unslid (ipsw dyld symaddr).
CF_STRING_CREATE = 0x18060ba00      # CFStringCreateWithCString(alloc, cStr, enc)
CF_SET_VALUE = 0x1806f8bac          # CFPreferencesSetValue(key, val, appID, user, host)
CF_SYNC = 0x1806d67d4               # CFPreferencesSynchronize(appID, user, host)
CF_BOOLEAN_TRUE_PTR = 0x1e0549de0   # &kCFBooleanTrue
ANY_USER_PTR = 0x1e054c0a8          # &kCFPreferencesAnyUser
ANY_HOST_PTR = 0x1e054c658          # &kCFPreferencesAnyHost
UTF8 = 0x08000100

BREAK = 0x1cac11c18                 # _BYSetupAssistantNeedsToRun entry (SpringBoard calls it)
KEYS = [b"BYBuddyFinishedInitialRunKey\x00", b"SetupFinishedAllSteps\x00", b"SetupDone\x00"]
APPID = b"com.apple.purplebuddy\x00"

STATE = {}
DONE = [False]


def _set(frame, name, value):
    if not frame.FindRegister(name).SetValueFromCString(hex(value & (2**64 - 1))):
        raise RuntimeError("cannot set " + name)


def _u64(process, address):
    error = lldb.SBError()
    data = process.ReadMemory(address, 8, error)
    if error.Fail():
        raise RuntimeError("read %#x: %s" % (address, error))
    return int.from_bytes(data, "little")


def _save_regs(frame):
    regs = {}
    for name in ([f"x{i}" for i in range(29)] + ["fp", "lr", "sp", "pc", "cpsr"]):
        v = frame.FindRegister(name)
        if v.IsValid():
            regs[name] = v.GetValueAsUnsigned()
    return regs


def _build_stages():
    # Each stage: (label, func, [args], result_slot). Args are ints or STATE keys.
    stages = [("mkAppID", CF_STRING_CREATE, [0, "appID_str", UTF8], "appID")]
    for i in range(len(KEYS)):
        stages.append(("mkKey%d" % i, CF_STRING_CREATE, [0, "key%d_str" % i, UTF8], "key%d" % i))
        stages.append(("setKey%d" % i, CF_SET_VALUE,
                       ["key%d" % i, "cfTrue", "appID", "anyUser", "anyHost"], None))
    stages.append(("sync", CF_SYNC, ["appID", "anyUser", "anyHost"], "sync"))
    return stages


def _arg(v):
    return v if isinstance(v, int) else STATE[v]


def _launch(frame):
    label, func, args, _slot = STATE["stages"][STATE["stage"]]
    _set(frame, "sp", STATE["call_sp"])
    names_order = ["x0", "x1", "x2", "x3", "x4"]
    for i in range(5):
        _set(frame, names_order[i], _arg(args[i]) if i < len(args) else 0)
    _set(frame, "lr", STATE["return_pc"])
    _set(frame, "pc", func + SLIDE)
    print("COMPLETE_BUDDY_CALL stage=%d %s" % (STATE["stage"], label), flush=True)


def on_break(frame, location, _dict):
    process = frame.GetThread().GetProcess()
    if DONE[0] or names._progname(process) != "SpringBoard":
        return False
    if not STATE:
        regs = _save_regs(frame)
        orig_sp = regs["sp"]
        scratch = (orig_sp - 0x2000) & ~0xF
        error = lldb.SBError()
        offs = {}
        addr = scratch
        for name, s in [("appID_str", APPID)] + [("key%d_str" % i, k) for i, k in enumerate(KEYS)]:
            process.WriteMemory(addr, s, error)
            if error.Fail():
                raise RuntimeError("write scratch: " + str(error))
            offs[name] = addr
            addr += (len(s) + 0xF) & ~0xF
        STATE.update(regs=regs, orig_sp=orig_sp, scratch=scratch,
                     call_sp=(orig_sp - 0x8000) & ~0xF, return_pc=BREAK + SLIDE,
                     stage=0, stages=_build_stages(),
                     cfTrue=_u64(process, CF_BOOLEAN_TRUE_PTR + SLIDE),
                     anyUser=_u64(process, ANY_USER_PTR + SLIDE),
                     anyHost=_u64(process, ANY_HOST_PTR + SLIDE), **offs)
        print("COMPLETE_BUDDY_START sp=%#x cfTrue=%#x anyUser=%#x anyHost=%#x"
              % (orig_sp, STATE["cfTrue"], STATE["anyUser"], STATE["anyHost"]), flush=True)
        _launch(frame)
        return False
    if frame.FindRegister("sp").GetValueAsUnsigned() > STATE["orig_sp"]:
        return False
    result = frame.FindRegister("x0").GetValueAsUnsigned()
    label, _f, _a, slot = STATE["stages"][STATE["stage"]]
    print("COMPLETE_BUDDY_RET stage=%d %s x0=%#x" % (STATE["stage"], label, result), flush=True)
    if slot:
        STATE[slot] = result
    STATE["stage"] += 1
    if STATE["stage"] < len(STATE["stages"]):
        _launch(frame)
        return False
    for name, value in STATE["regs"].items():
        _set(frame, name, value)
    location.GetBreakpoint().SetEnabled(False)
    DONE[0] = True
    print("COMPLETE_BUDDY_DONE preferences written AnyUser/AnyHost and synchronized", flush=True)
    return False


def install(debugger, slide):
    global SLIDE
    SLIDE = slide
    names.SLIDE[0] = slide
    target = debugger.GetSelectedTarget()
    bp = target.BreakpointCreateByAddress(BREAK + slide)
    bp.SetScriptCallbackFunction("complete_buddy_callbacks.on_break")
    print("COMPLETE_BUDDY_READY bp=%d at %#x" % (bp.GetID(), BREAK + slide), flush=True)
