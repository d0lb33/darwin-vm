"""Complete Buddy AND set the device-default wallpaper on a fresh un-personalized
native-smc disk, in one SpringBoard hijack.

Corrected target: the earlier hang was `objc_msgSend(WKDefaultWallpaperManager,
@selector(sharedInstance))` -- WKDefaultWallpaperManager's accessor is actually
`+defaultWallpaperManager`, and it does NOT own the restore method, so that was
an unrecognized selector whose exception path hung at early launch.

The real API is a no-argument CLASS method (class-dumped from the extracted
framework): `+[PBUIWallpaperService restoreDefaultWallpaper]` (SpringBoard's
own `+[SBSUIWallpaperService restoreDefaultWallpaper]` is the sibling client
entry). It reads `/Library/Wallpaper/DefaultWallpapers~iphone.plist` and applies
this device's default (collection 62879BFA "iOS 17" / wallpaper 7565) to all
variants. No instance, no accessor.

It is invoked via `performSelectorInBackground:withObject:` so it runs on a
spawned thread: SpringBoard's main thread is released immediately, so if the
service's work hops to the SpringBoard wallpaper server (same process, main
thread) it is serviced instead of deadlocking.

Stages: mobile-domain purplebuddy completion (CFPreferencesSetAppValue x3 +
CFPreferencesAppSynchronize -> TRUE), then the background restore dispatch.
Registers saved/restored. Debugger-driven; not a default image change.
"""
import lldb
import welcome_abort_callbacks as names

SLIDE = 0

CF_STRING_CREATE = 0x18060ba00
CF_SET_APP_VALUE = 0x1806c8088
CF_APP_SYNC = 0x1806cfc2c
CF_BOOLEAN_TRUE_PTR = 0x1e0549de0
UTF8 = 0x08000100

OBJC_MSGSEND = 0x188000800
SEL_REGISTERNAME = 0x18040e6d8

# _OBJC_CLASS_$_SBSUIWallpaperService (unslid; SpringBoardUIServices, which
# SpringBoard links). +[SBSUIWallpaperService restoreDefaultWallpaper] IMP is at
# 0x1a85584c8. Class-dumped from the extracted framework.
SVC_CLASS = 0x1e86699f0

BREAK = 0x1cac11c18                 # _BYSetupAssistantNeedsToRun

APPID = b"com.apple.purplebuddy\x00"
KEYS = [b"BYBuddyFinishedInitialRunKey\x00", b"SetupFinishedAllSteps\x00",
        b"SetupDone\x00"]
RESTORE = b"restoreDefaultWallpaper\x00"          # no-arg class method
# Run on SpringBoard's MAIN thread (has a runloop for the wallpaper XPC reply)
# but async (waitUntilDone:NO) so it fires once the runloop is up post-launch,
# with no deadlock. performSelectorInBackground gave the thread no runloop, so
# the restore's XPC reply was never delivered and nothing persisted.
PSIB = b"performSelectorOnMainThread:withObject:waitUntilDone:\x00"

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
    stages = [("mkAppID", CF_STRING_CREATE, [0, "appID_str", UTF8], "appID")]
    for i in range(len(KEYS)):
        stages.append(("mkKey%d" % i, CF_STRING_CREATE,
                       [0, "key%d_str" % i, UTF8], "key%d" % i))
        stages.append(("setKey%d" % i, CF_SET_APP_VALUE,
                       ["key%d" % i, "cfTrue", "appID"], None))
    stages.append(("sync", CF_APP_SYNC, ["appID"], "sync"))
    stages.append(("selRestore", SEL_REGISTERNAME, ["restore_str"], "selRestore"))
    stages.append(("selPSIB", SEL_REGISTERNAME, ["psib_str"], "selPSIB"))
    # [SBSUIWallpaperService performSelectorOnMainThread:@selector(restoreDefaultWallpaper)
    #                        withObject:nil waitUntilDone:NO]
    stages.append(("bgRestore", OBJC_MSGSEND,
                   ["svcClass", "selPSIB", "selRestore", 0, 0], "bgRet"))
    return stages


def _arg(v):
    return v if isinstance(v, int) else STATE[v]


def _launch(frame):
    label, func, args, _slot = STATE["stages"][STATE["stage"]]
    _set(frame, "sp", STATE["call_sp"])
    order = ["x0", "x1", "x2", "x3", "x4"]
    for i in range(5):
        _set(frame, order[i], _arg(args[i]) if i < len(args) else 0)
    _set(frame, "lr", STATE["return_pc"])
    _set(frame, "pc", func + SLIDE)
    print("SEED_WP_CALL stage=%d %s" % (STATE["stage"], label), flush=True)


def on_break(frame, location, _dict):
    process = frame.GetThread().GetProcess()
    if DONE[0] or names._progname(process) != "SpringBoard":
        return False
    tid = frame.GetThread().GetThreadID()
    if not STATE:
        regs = _save_regs(frame)
        orig_sp = regs["sp"]
        scratch = (orig_sp - 0x2000) & ~0xF
        error = lldb.SBError()
        offs = {}
        addr = scratch
        strings = ([("appID_str", APPID)] +
                   [("key%d_str" % i, k) for i, k in enumerate(KEYS)] +
                   [("restore_str", RESTORE), ("psib_str", PSIB)])
        for name, s in strings:
            process.WriteMemory(addr, s, error)
            if error.Fail():
                raise RuntimeError("write scratch: " + str(error))
            offs[name] = addr
            addr += (len(s) + 0xF) & ~0xF
        STATE.update(regs=regs, orig_sp=orig_sp, tid=tid,
                     call_sp=(orig_sp - 0x8000) & ~0xF, return_pc=BREAK + SLIDE,
                     stage=0, stages=_build_stages(), svcClass=SVC_CLASS + SLIDE,
                     cfTrue=_u64(process, CF_BOOLEAN_TRUE_PTR + SLIDE), **offs)
        print("SEED_WP_START tid=%d sp=%#x svcClass=%#x" %
              (tid, orig_sp, STATE["svcClass"]), flush=True)
        _launch(frame)
        return False
    if tid != STATE["tid"]:
        return False
    if frame.FindRegister("sp").GetValueAsUnsigned() > STATE["orig_sp"]:
        return False
    result = frame.FindRegister("x0").GetValueAsUnsigned()
    label, _f, _a, slot = STATE["stages"][STATE["stage"]]
    print("SEED_WP_RET stage=%d %s x0=%#x" % (STATE["stage"], label, result), flush=True)
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
    print("SEED_WP_DONE buddy-complete + background restoreDefaultWallpaper "
          "dispatched (sync=%#x)" % STATE.get("sync", 0), flush=True)
    return False


def install(debugger, slide):
    global SLIDE
    SLIDE = slide
    names.SLIDE[0] = slide
    if not SVC_CLASS:
        raise RuntimeError("SVC_CLASS address not filled in")
    target = debugger.GetSelectedTarget()
    bp = target.BreakpointCreateByAddress(BREAK + slide)
    bp.SetScriptCallbackFunction("seed_wallpaper_bg.on_break")
    print("SEED_WP_READY bp=%d at %#x svcClass=%#x" %
          (bp.GetID(), BREAK + slide, SVC_CLASS + slide), flush=True)
