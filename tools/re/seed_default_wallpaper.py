"""Complete Buddy AND seed the device-default lock/home wallpaper in one
SpringBoard hijack, on a fresh un-personalized native-smc disk.

Why: the base image ships the wallpaper content
(`/Library/Wallpaper/Collections/iOS_17~iphone.wallpaperCollection`, collection
`62879BFA-F700-4384-83E1-7AD1CC0D0817` "iOS 17", wallpaper 7565 -- a
CoreAnimation `.ca` wallpaper whose 393w-852h@3x variant matches our 1179x2556
panel) and `DefaultWallpapers~iphone.plist` names it as this device's default,
but the first-boot Setup step that *selects* it never ran. Buddy completion
alone (SETUP2/SETUP6) reaches the lock screen but leaves an empty Collections
poster, so the wallpaper renders solid black.

At SpringBoard's `BYSetupAssistantNeedsToRun` call (early in launch, before the
wallpaper is loaded) this runs, on SpringBoard's own thread:

  1. CFPreferencesSetAppValue(<3 purplebuddy keys>=true, com.apple.purplebuddy)
     + CFPreferencesAppSynchronize    -- mark Setup complete (proven path)
  2. [[WKDefaultWallpaperManager sharedInstance]
        restoreDefaultWallpaperForAllVariantsAndNotify:YES]
     -- read DefaultWallpapers~iphone.plist and install the device default for
        both variants, so SpringBoard's later wallpaper load finds real content.

All registers saved/restored; SpringBoard resumes where it was. Debugger-driven,
verified by a boot; not a default image change.

Addresses are unslid VAs from `ipsw dyld symaddr` on this exact cache.
"""
import lldb
import welcome_abort_callbacks as names

SLIDE = 0

# CoreFoundation (unslid).
CF_STRING_CREATE = 0x18060ba00      # CFStringCreateWithCString(alloc, cStr, enc)
CF_SET_APP_VALUE = 0x1806c8088      # CFPreferencesSetAppValue(key, value, appID)
CF_APP_SYNC = 0x1806cfc2c           # CFPreferencesAppSynchronize(appID)
CF_BOOLEAN_TRUE_PTR = 0x1e0549de0   # &kCFBooleanTrue
UTF8 = 0x08000100

# libobjc / libobjcMsgSend / WallpaperKit (unslid).
OBJC_MSGSEND = 0x188000800          # _objc_msgSend  (libobjcMsgSend.dylib)
SEL_REGISTERNAME = 0x18040e6d8      # _sel_registerName
WK_CLASS = 0x1e81b3650              # _OBJC_CLASS_$_WKDefaultWallpaperManager

BREAK = 0x1cac11c18                 # _BYSetupAssistantNeedsToRun entry

APPID = b"com.apple.purplebuddy\x00"
KEYS = [b"BYBuddyFinishedInitialRunKey\x00", b"SetupFinishedAllSteps\x00",
        b"SetupDone\x00"]
SHARED = b"sharedInstance\x00"
RESTORE = b"restoreDefaultWallpaperForAllVariantsAndNotify:\x00"

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
    # (label, func, [args], result_slot); args are ints or STATE keys.
    stages = [("mkAppID", CF_STRING_CREATE, [0, "appID_str", UTF8], "appID")]
    for i in range(len(KEYS)):
        stages.append(("mkKey%d" % i, CF_STRING_CREATE,
                       [0, "key%d_str" % i, UTF8], "key%d" % i))
        stages.append(("setKey%d" % i, CF_SET_APP_VALUE,
                       ["key%d" % i, "cfTrue", "appID"], None))
    stages.append(("sync", CF_APP_SYNC, ["appID"], "sync"))
    # Wallpaper default-restore via WallpaperKit.
    stages.append(("selShared", SEL_REGISTERNAME, ["shared_str"], "selShared"))
    stages.append(("getMgr", OBJC_MSGSEND, ["wkClass", "selShared"], "mgr"))
    stages.append(("selRestore", SEL_REGISTERNAME, ["restore_str"], "selRestore"))
    stages.append(("restore", OBJC_MSGSEND, ["mgr", "selRestore", 1], "restoreRet"))
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
                   [("shared_str", SHARED), ("restore_str", RESTORE)])
        for name, s in strings:
            process.WriteMemory(addr, s, error)
            if error.Fail():
                raise RuntimeError("write scratch: " + str(error))
            offs[name] = addr
            addr += (len(s) + 0xF) & ~0xF
        STATE.update(regs=regs, orig_sp=orig_sp, tid=tid,
                     call_sp=(orig_sp - 0x8000) & ~0xF, return_pc=BREAK + SLIDE,
                     stage=0, stages=_build_stages(), wkClass=WK_CLASS + SLIDE,
                     cfTrue=_u64(process, CF_BOOLEAN_TRUE_PTR + SLIDE), **offs)
        print("SEED_WP_START tid=%d sp=%#x cfTrue=%#x wkClass=%#x" %
              (tid, orig_sp, STATE["cfTrue"], STATE["wkClass"]), flush=True)
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
    print("SEED_WP_DONE buddy-complete + restoreDefaultWallpaper invoked "
          "(mgr=%#x sync=%#x)" % (STATE.get("mgr", 0), STATE.get("sync", 0)),
          flush=True)
    return False


def install(debugger, slide):
    global SLIDE
    SLIDE = slide
    names.SLIDE[0] = slide
    target = debugger.GetSelectedTarget()
    bp = target.BreakpointCreateByAddress(BREAK + slide)
    bp.SetScriptCallbackFunction("seed_default_wallpaper.on_break")
    print("SEED_WP_READY bp=%d at %#x (msgSend=%#x wkClass=%#x)" %
          (bp.GetID(), BREAK + slide, OBJC_MSGSEND + slide, WK_CLASS + slide),
          flush=True)
