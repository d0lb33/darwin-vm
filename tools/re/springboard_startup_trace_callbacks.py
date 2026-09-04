"""Bounded same-boot trace from SpringBoard's first display-list reply.

The old ``QD_CASGETDISPLAYS_RET`` witness at 0x1844e484c proves only that
the CoreAnimation MIG call returned.  ``query_displays`` then performs a
substantial amount of local display-list processing before its real return at
0x1844e4d5c.  This tracer captures SpringBoard's originating thread at the MIG
return, enables every direct call site in the rest of ``query_displays`` and
``+[CADisplay mainDisplay]``, and plants a filtered return witness for each
call reached.  The last call without a matching return identifies the next
bounded startup stall in one boot.

All TRACE_JSON records are read-only evidence.  Breakpoints are disabled until
the SpringBoard anchor fires so shared-cache traffic from other processes does
not dominate the run.
"""
import json
import os
import time

import lldb


SLIDE = [0]
PROGNAME_PTR = 0x1e6ef1590
CACHE_LO = 0x180000000
CACHE_HI = 0x340000000
MAX_CALLS = int(os.environ.get("DVM_SB_TRACE_MAX_CALLS", "256"), 0)
MAX_SITE_HITS = max(1, int(os.environ.get(
    "DVM_SB_TRACE_MAX_SITE_HITS", "1"), 0))
DYNAMIC_SCAN_BYTES = max(4, int(os.environ.get(
    "DVM_SB_TRACE_DYNAMIC_SCAN_BYTES", "0x800"), 0))
MAX_DYNAMIC_TARGETS = max(1, int(os.environ.get(
    "DVM_SB_TRACE_MAX_DYNAMIC_TARGETS", "64"), 0))
MAX_WAIT_CONTROLS = max(1, int(os.environ.get(
    "DVM_SB_TRACE_MAX_WAIT_CONTROLS", "64"), 0))
FOLLOW_DYNAMIC = os.environ.get("DVM_SB_TRACE_FOLLOW_DYNAMIC", "1") != "0"
EXPAND_CCS_REPOSITORY = os.environ.get(
    "DVM_SB_TRACE_EXPAND_CCS_REPOSITORY", "0") != "0"
STOP_ON_SUCCESS = os.environ.get(
    "DVM_SB_TRACE_STOP_ON_SUCCESS", "0") != "0"
PROGRESS_ONLY = os.environ.get(
    "DVM_SB_TRACE_PROGRESS_ONLY", "0") != "0"
STOP_ON_WAIT = os.environ.get(
    "DVM_SB_TRACE_STOP_ON_WAIT", "0") != "0"
ROOT_LABEL = os.environ.get("DVM_SB_TRACE_ROOT_LABEL", "")
SEEDED_TPIDR = int(os.environ["DVM_SB_TRACE_TPIDR"], 0) if os.environ.get(
    "DVM_SB_TRACE_TPIDR") else None
SEEDED_STACK_REGION = int(os.environ["DVM_SB_TRACE_STACK_REGION"], 0) if os.environ.get(
    "DVM_SB_TRACE_STACK_REGION") else None
EVENT_DIR = os.environ.get("DVM_PROBE_EVENT_DIR", "")
SUCCESS_LABELS = set(filter(None, os.environ.get(
    "DVM_PROBE_SUCCESS_LABELS", "").split(",")))
TRACE_SELECTOR = int(os.environ.get("DVM_SB_TRACE_PENDING_SELECTOR", "0xcc5"), 0)

CONFIG = {}
RETURNS = {}
ACTIVE_CALLS = {}
CALL_SITES = {}
ENTRY_POINTS = {}
OBJC_DISPATCHES = {}
WAIT_BREAKPOINT_IDS = []
WAIT_RETURNS = {}
SEEDED_WAIT_RETURN_STATIC = 0x237cd3308
OBJC_STUBS_TO_RESOLVE = {
    int(value, 0) for value in os.environ.get(
        "DVM_SB_TRACE_OBJC_STUBS", "0x188118b30").split(",") if value
}
WAIT_PRIMITIVES = {
    0x237ccfccc: "mach_msg2_trap",
    0x237ccfc0c: "mach_msg_trap",
    0x237ccfc48: "semaphore_wait_trap",
    0x237ccfc60: "semaphore_timedwait_trap",
    0x237cd041c: "kevent_qos",
    0x1ae8bae94: "dispatch_semaphore_wait",
    0x1ae8d4690: "dispatch_mach_send_and_wait_for_reply",
}
GLOBAL_PROGRESS_LABELS = {
    "CADISPLAY_MAIN_ENTRY",
    "CADISPLAY_MAIN_RETURN",
    "FB_SYSTEM_SHELL_ENTRY",
    "QUERY_DISPLAYS_RETURN",
    "ENSURE_DISPLAYS_AFTER_QUERY",
    "ENSURE_DISPLAYS_RETURN",
    "FB_SYSTEM_SHELL_RETURN",
    "FB_CREATE_SINGLETON_ENTRY",
    "FB_CREATE_SINGLETON_BLOCK_ENTRY",
    "FB_INIT_OPTIONS_ENTRY",
    "FB_INIT_OPTIONS_PAGE_C0",
    "SB_BEFORE_FB_SYSTEM_SHELL",
    "SB_AFTER_FB_SYSTEM_SHELL",
    "SB_BEFORE_UIAPPLICATION_MAIN",
    "UIAPPLICATION_MAIN",
    "SB_ADFL_ENTRY",
    "SB_FINALIZE_ENTRY",
    "SB_UPDATE_ENTRY",
    "SB_SET_REASON_ENTRY",
    "SB_SETUPAPP_ENTRY",
    "SB_ACTIVATE_ENTRY",
    "SPRINGBOARD_ABORT",
    "SPRINGBOARD_EXIT",
}
INDIRECT_CALLS = {
    # query_displays allocation helper
    0x1844e4940: ("blraa", "x16", "x17"),
    # _FBSystemShellInitialize callback interfaces
    0x1c4a3bea8: ("blraa", "x9", "x8"),
    0x1c4a3bfc8: ("blraa", "x9", "x8"),
}

# Function extents established from the extracted iOS 27 dyld cache.  These
# are deliberately narrow: an entry hit on the captured SpringBoard thread
# expands the trace inside that function during the same boot.  Unknown
# targets still get entry/return witnesses, but are not scanned speculatively.
KNOWN_RANGES = {
    0x1c4a15e40: (0x1c4a15f20, "fb_create_singleton",
                  "FrontBoard", "+[FBSystemShell _createSingletonWithOptions:]"),
    0x1c4a15f20: (0x1c4a15f70, "fb_create_singleton_block",
                  "FrontBoard", "FBSystemShell singleton dispatch_once block"),
    0x1c4a158e4: (0x1c4a15c00, "fb_init_options",
                  "FrontBoard", "-[FBSystemShell _initWithOptions:]"),
    0x1c4a15c00: (0x1c4a15ca0, "fb_init_options_c0",
                   "FrontBoard", "-[FBSystemShell _initWithOptions:] page c0"),
    0x1c4a15cc0: (0x1c4a15d4c, "fb_init_options_block_1",
                  "FrontBoard", "FBSystemShell options callback block 1"),
    0x1c4a15d4c: (0x1c4a15e40, "fb_init_options_block_2",
                  "FrontBoard", "FBSystemShell options callback block 2"),
    0x186dc8bcc: (0x186dc8bd0, "ls_context_init_options",
                  "CoreServices", "_LSContextInitWithOptions"),
    0x186dc8bd0: (0x186dc9668, "ls_context_init_common",
                  "CoreServices", "_LSContextInitCommon"),
    0x186dc9668: (0x186dc96b0, "ls_default_service_domain",
                  "CoreServices", "+[_LSDServiceDomain defaultServiceDomain]"),
    0x186dc96b0: (0x186dc9798, "ls_database_context_get",
                  "CoreServices", "LaunchServices::Database::Context::_get"),
    0x186dc8768: (0x186dc8bcc, "ls_application_record_init_core",
                  "CoreServices", "-[LSApplicationRecord _initWithNode:bundleIdentifier:placeholderBehavior:systemPlaceholder:itemID:forceInBundleContainer:context:error:]"),
    0x186dca928: (0x186dcab5c, "ls_application_record_init_block",
                  "CoreServices", "LSApplicationRecord database lookup block"),
    0x186dd1348: (0x186dd1388, "ls_application_record_init_fetching_placeholder",
                  "CoreServices", "-[LSApplicationRecord initWithBundleIdentifier:fetchingPlaceholder:error:]"),
    0x186dd29e4: (0x186dd29ec, "ls_application_record_init_allow_placeholder",
                  "CoreServices", "-[LSApplicationRecord initWithBundleIdentifier:allowPlaceholder:error:]"),
    0x186df21c8: (0x186df2b04, "ls_copy_server_store",
                  "CoreServices", "_LSCopyServerStore"),
    0x186df2b04: (0x186df2c40, "ls_copy_server_store_block",
                  "CoreServices", "_LSCopyServerStore reply block"),
    0x186df15b0: (0x186df171c, "ls_read_client_get_server_store",
                  "CoreServices", "-[_LSDReadClient getServerStoreNonBlockingWithCompletionHandler:]"),
    0x186df1e04: (0x186df2168, "ls_server_get_store_for_connection",
                  "CoreServices", "_LSServer_GetServerStoreForConnectionWithCompletionHandler"),
    0x259097d0c: (0x259097da4, "ccs_start_services",
                  "ControlCenterServices", "+[CCSControlCenterServicesManager startServices]"),
    0x259097de0: (0x259097fb0, "ccs_start_services_body",
                  "ControlCenterServices", "startServices body"),
    0x25908a868: (0x25908a8ac, "ccs_remote_shared",
                  "ControlCenterServices", "+[CCSRemoteServiceProvider sharedInstance]"),
    0x25908a8ac: (0x25908a90c, "ccs_remote_shared_once",
                  "ControlCenterServices", "CCSRemoteServiceProvider sharedInstance once block"),
    0x25908a90c: (0x25908a9e0, "ccs_remote_init",
                  "ControlCenterServices", "-[CCSRemoteServiceProvider _init]"),
    0x25908ab00: (0x25908ab08, "ccs_remote_resume",
                  "ControlCenterServices", "-[CCSRemoteServiceProvider resume]"),
    0x2590953c8: (0x259095468, "ccs_repository_shared",
                  "ControlCenterServices", "+[CCSModuleRepository sharedInstance]"),
    0x259095468: (0x2590954f4, "ccs_repository_shared_once",
                  "ControlCenterServices", "CCSModuleRepository sharedInstance once block"),
    0x259095544: (0x2590956d4, "ccs_repository_init",
                  "ControlCenterServices", "-[CCSModuleRepository _initWithDirectoryURLs:allowedModuleIdentifiers:]"),
    0x2590956d4: (0x25909576c, "ccs_repository_init_block",
                  "ControlCenterServices", "CCSModuleRepository initialization dispatch block"),
    0x25909602c: (0x25909608c, "ccs_repository_update_all",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_updateAllModuleMetadata]"),
    0x259096294: (0x25909642c, "ccs_repository_update_all_for_all",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_updateAllModuleMetadataForAllModuleMetadata:]"),
    0x25909642c: (0x259096500, "ccs_repository_update_available_for_all",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_updateAvailableModuleMetadataForAllModuleMetadata:]"),
    0x259096500: (0x25909675c, "ccs_repository_update_loadable_for_available",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_updateLoadableModuleMetadataForAvailableModuleMetadata:]"),
    0x25909675c: (0x259096768, "ccs_repository_update_loadable_block",
                  "ControlCenterServices", "CCSModuleRepository update-loadable completion block"),
    0x259096768: (0x2590968d4, "ccs_repository_module_identifiers",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_moduleIdentifiersForMetadata:]"),
    0x2590968d4: (0x2590969bc, "ccs_repository_load_all",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_loadAllModuleMetadata]"),
    0x2590969bc: (0x259096a38, "ccs_repository_load_all_block_1",
                  "ControlCenterServices", "CCSModuleRepository load-all enumeration block 1"),
    0x259096a38: (0x259096a80, "ccs_repository_load_all_block_2",
                  "ControlCenterServices", "CCSModuleRepository load-all enumeration block 2"),
    0x259096a80: (0x259096c58, "ccs_repository_load_all_block_3",
                  "ControlCenterServices", "CCSModuleRepository load-all enumeration block 3"),
    0x259096c58: (0x259096cb8, "ccs_repository_filter_bundle_availability",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_filterModuleMetadataByAssociatedBundleAvailability:]"),
    0x259096cb8: (0x259096e60, "ccs_repository_filter_bundle_availability_block",
                  "ControlCenterServices", "CCSModuleRepository bundle-availability filter block"),
    0x259096e60: (0x259096ecc, "ccs_repository_update_interesting_bundles",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_updateInterestingBundleIdentifiersForModuleMetadata:]"),
    0x259096ecc: (0x259097038, "ccs_repository_associated_bundles",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_associatedBundleIdentifiersForModuleMetadata:]"),
    0x259097038: (0x2590970dc, "ccs_repository_has_interesting_proxy",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_arrayContainsInterestingApplicationProxy:]"),
    0x2590970dc: (0x259097128, "ccs_repository_has_interesting_proxy_block",
                  "ControlCenterServices", "CCSModuleRepository interesting-application filter block"),
    0x259097128: (0x259097188, "ccs_repository_filter_visibility",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_filterModuleMetadataByVisibilityPreference:]"),
    0x259097188: (0x2590971f8, "ccs_repository_filter_visibility_block",
                  "ControlCenterServices", "CCSModuleRepository visibility filter block"),
    0x2590972ac: (0x259097438, "ccs_repository_filter_gestalt",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_filterModuleMetadataByGestalt:]"),
    0x259097438: (0x259097458, "ccs_repository_filter_gestalt_block_1",
                  "ControlCenterServices", "CCSModuleRepository gestalt filter block 1"),
    0x259097458: (0x25909751c, "ccs_repository_filter_gestalt_block_2",
                  "ControlCenterServices", "CCSModuleRepository gestalt filter block 2"),
    0x25909751c: (0x259097618, "ccs_repository_update_gestalt_questions",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_updateGestaltQuestionsForModuleMetadata:]"),
    0x259097618: (0x259097658, "ccs_repository_update_gestalt_questions_block",
                  "ControlCenterServices", "CCSModuleRepository gestalt-question enumeration block"),
    0x259097658: (0x2590977d4, "ccs_repository_gestalt_questions",
                  "ControlCenterServices", "-[CCSModuleRepository _queue_gestaltQuestionsForModuleMetadata:]"),
    0x259092828: (0x25909297c, "ccs_settings_init",
                  "ControlCenterServices", "-[CCSModuleSettingsProvider init]"),
}
TRACE = {
    "tpidr_el1": None,
    "x18": None,
    "stack_regions": set(),
    "contexts": set(),
    "thread": None,
    "started": None,
    "calls": 0,
    "dynamic_targets": 0,
    "wait_root_call": None,
    "wait_sequence": 0,
    "wait_controls": 0,
}


def _reg(frame, name):
    value = frame.FindRegister(name)
    return value.GetValueAsUnsigned() if value.IsValid() else None


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    return data if error.Success() else b""


def _u64(process, address):
    data = _read(process, address, 8)
    return int.from_bytes(data, "little") if len(data) == 8 else None


def _cstring(process, address, limit=96):
    if not address:
        return "<null>"
    data = _read(process, address, limit)
    return (data.split(b"\0", 1)[0].decode("ascii", "replace")
            if data else "<read-error>")


def _progname(process):
    pointer = _u64(process, SLIDE[0] + PROGNAME_PTR)
    string = _u64(process, pointer) if pointer else None
    return _cstring(process, string)


def _static(address):
    address = _canonical(address)
    slide = SLIDE[0]
    if address is not None and CACHE_LO + slide <= address < CACHE_HI + slide:
        return address - slide
    return None


def _canonical(address):
    """Strip arm64e PAC bits while retaining canonical kernel addresses."""
    if address is None:
        return None
    value = address & 0x0000ffffffffffff
    if value & (1 << 47):
        value |= 0xffff000000000000
    return value


def _write_event(name, payload):
    if not EVENT_DIR:
        return
    os.makedirs(EVENT_DIR, exist_ok=True)
    path = os.path.join(EVENT_DIR, name)
    temporary = "%s.%d.tmp" % (path, os.getpid())
    with open(temporary, "w") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _remove_event(name):
    if not EVENT_DIR:
        return
    try:
        os.unlink(os.path.join(EVENT_DIR, name))
    except FileNotFoundError:
        pass


def _refresh_pending(progress):
    """Arm an inactivity deadline for the deepest currently active call.

    A deadline tied to the outer startServices call expires while LLDB is
    still making valid forward progress through metadata and database code.
    Refreshing one event at every nested entry/return instead makes the probe
    stop only after the traced chain has been quiet for the configured period.
    """
    if not ACTIVE_CALLS:
        _remove_event("pending.trace.json")
        return
    call_id = max(ACTIVE_CALLS)
    active = ACTIVE_CALLS[call_id]
    _write_event("pending.trace.json", {
        "call_id": call_id,
        "selector": TRACE_SELECTOR,
        "time": time.time(),
        "progress": progress,
        "scope": active["scope"],
        "callsite_static": active["callsite_static"],
        "target_static": active["target_static"],
    })


def _emit(kind, **fields):
    record = {
        "kind": kind,
        "epoch": time.time(),
        "elapsed": (time.time() - TRACE["started"]
                    if TRACE["started"] is not None else None),
        "thread": TRACE["thread"],
        "tpidr_el1": TRACE["tpidr_el1"],
        "x18": TRACE["x18"],
        "stack_regions": [hex(value) for value in sorted(TRACE["stack_regions"])],
    }
    record.update(fields)
    print("TRACE_JSON " + json.dumps(record, sort_keys=True), flush=True)


def _same_thread(frame):
    stack_region = (_reg(frame, "sp") or 0) & ~0xfffff
    return (TRACE["tpidr_el1"] is not None and
            _reg(frame, "tpidr_el1") == TRACE["tpidr_el1"] and
            stack_region in TRACE["stack_regions"] and
            _progname(frame.GetThread().GetProcess()) == "SpringBoard")


def _same_thread_fast(frame):
    """Match an already captured thread without remote memory reads.

    Breakpoints inside objc_msgSend's cache dispatch path must finish quickly:
    while LLDB is stopped at the final ``br x17``, even a harmless process-name
    ReadMemory can block long enough for the probe inactivity watchdog to fire.
    The anchor already positively identified SpringBoard, so tpidr_el1 plus its
    captured 1 MiB stack region is the sufficient bounded hot-path filter.
    """
    stack_region = (_reg(frame, "sp") or 0) & ~0xfffff
    return (TRACE["tpidr_el1"] is not None and
            _reg(frame, "tpidr_el1") == TRACE["tpidr_el1"] and
            stack_region in TRACE["stack_regions"])


def _backtrace(thread, limit=24):
    frames = []
    for index in range(min(thread.GetNumFrames(), limit)):
        pc = thread.GetFrameAtIndex(index).GetPC() & 0x0000ffffffffffff
        frames.append({"runtime": pc, "static": _static(pc)})
    return frames


def _hex_read(process, address, size, limit=512):
    """Return a bounded read as hex without making evidence look complete."""
    requested = max(0, min(int(size), limit))
    data = _read(process, address, requested) if address and requested else b""
    return {
        "address": address,
        "requested": requested,
        "read": len(data),
        "hex": data.hex(),
    }


def _mach_msg2_arguments(process, registers):
    """Decode the public mach_msg2 packed ABI and its optional vectors."""
    options = registers["x1"] or 0
    bits_and_send = registers["x2"] or 0
    remote_and_local = registers["x3"] or 0
    voucher_and_id = registers["x4"] or 0
    descriptors_and_receive = registers["x5"] or 0
    receive_size_and_priority = registers["x6"] or 0
    decoded = {
        "data": registers["x0"],
        "options": options,
        "msgh_bits": bits_and_send & 0xffffffff,
        "send_size_or_vector_count": bits_and_send >> 32,
        "remote_port": remote_and_local & 0xffffffff,
        "local_port": remote_and_local >> 32,
        "voucher_port": voucher_and_id & 0xffffffff,
        "msgh_id": voucher_and_id >> 32,
        "descriptor_count": descriptors_and_receive & 0xffffffff,
        "receive_name": descriptors_and_receive >> 32,
        "receive_size_or_vector_count": receive_size_and_priority & 0xffffffff,
        "priority": receive_size_and_priority >> 32,
        "timeout": registers["x7"],
        "vector": bool(options & (1 << 32)),
    }
    if not decoded["vector"]:
        decoded["message"] = _hex_read(
            process, registers["x0"], decoded["send_size_or_vector_count"])
        return decoded

    count = max(decoded["send_size_or_vector_count"],
                decoded["receive_size_or_vector_count"])
    raw = _read(process, registers["x0"], min(count, 2) * 24)
    decoded["vectors_raw"] = raw.hex()
    vectors = []
    for offset in range(0, len(raw) - 23, 24):
        data_address = int.from_bytes(raw[offset:offset + 8], "little")
        receive_address = int.from_bytes(raw[offset + 8:offset + 16], "little")
        send_size = int.from_bytes(raw[offset + 16:offset + 20], "little")
        receive_size = int.from_bytes(raw[offset + 20:offset + 24], "little")
        vectors.append({
            "data": data_address,
            "receive_address": receive_address,
            "send_size": send_size,
            "receive_size": receive_size,
            "send_data": _hex_read(process, data_address, send_size),
        })
    decoded["vectors"] = vectors
    return decoded


def _decode_bl(static_pc, word):
    if word >> 26 != 0b100101:
        return None
    immediate = word & 0x03ffffff
    if immediate & 0x02000000:
        immediate -= 0x04000000
    return (static_pc + (immediate << 2)) & 0xffffffffffffffff


def _objc_stub_selector(process, target_runtime, target_static):
    """Decode the selector materialized by a standard objc message stub.

    The caller's x1 is not the selector: these cache stubs load it with an
    ADRP/ADD pair immediately before tail-branching to objc_msgSend.  Resolve
    that pair from live code so the dispatch breakpoint can retain an exact
    selector filter without hard-coding this particular method.
    """
    data = _read(process, target_runtime, 8)
    if len(data) != 8 or target_static is None:
        return None
    adrp = int.from_bytes(data[:4], "little")
    add = int.from_bytes(data[4:], "little")
    if adrp & 0x9f000000 != 0x90000000:
        return None
    register = adrp & 31
    if (add & 0xff800000 != 0x91000000 or
            add & 31 != register or (add >> 5) & 31 != register):
        return None
    immediate = (((adrp >> 5) & 0x7ffff) << 2) | ((adrp >> 29) & 3)
    if immediate & (1 << 20):
        immediate -= 1 << 21
    page = (target_static & ~0xfff) + (immediate << 12)
    offset = ((add >> 10) & 0xfff) << (12 if add & (1 << 22) else 0)
    return SLIDE[0] + page + offset


def _decode_control(static_pc, word, range_start, range_end):
    """Decode calls plus only those branches that leave the scanned function."""
    target = _decode_bl(static_pc, word)
    if target is not None:
        return {"mnemonic": "bl", "target_static": target, "tail": False}
    if word & 0xfffffc1f == 0xd63f0000:
        return {"mnemonic": "blr", "target_register": "x%d" % ((word >> 5) & 31),
                "modifier_register": None, "tail": False}
    if word & 0xfffff800 == 0xd73f0800:
        return {"mnemonic": "blraa", "target_register": "x%d" % ((word >> 5) & 31),
                "modifier_register": "x%d" % (word & 31), "tail": False}
    # Follow a direct tail branch only when it leaves this bounded function.
    if word >> 26 == 0b000101:
        immediate = word & 0x03ffffff
        if immediate & 0x02000000:
            immediate -= 0x04000000
        target = (static_pc + (immediate << 2)) & 0xffffffffffffffff
        if not range_start <= target < range_end:
            return {"mnemonic": "b", "target_static": target, "tail": True}
    if word & 0xfffffc1f == 0xd61f0000:
        return {"mnemonic": "br", "target_register": "x%d" % ((word >> 5) & 31),
                "modifier_register": None, "tail": True}
    if word & 0xfffff800 == 0xd71f0800:
        return {"mnemonic": "braa", "target_register": "x%d" % ((word >> 5) & 31),
                "modifier_register": "x%d" % (word & 31), "tail": True}
    return None


def _install(target, address, label, callback, enabled=True):
    breakpoint = target.BreakpointCreateByAddress(address)
    if not breakpoint.IsValid() or breakpoint.GetNumLocations() != 1:
        raise RuntimeError("failed breakpoint %s at 0x%x" % (label, address))
    breakpoint.SetScriptCallbackFunction(
        "springboard_startup_trace_callbacks.%s" % callback)
    breakpoint.SetEnabled(enabled)
    CONFIG[breakpoint.GetID()] = {"label": label, "address": address}
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x enabled=%d" %
          (breakpoint.GetID(), label, address, int(enabled)))
    return breakpoint


def _enable_calls(target, scope):
    for breakpoint_id, config in CONFIG.items():
        if config.get("scope") == scope:
            target.FindBreakpointByID(breakpoint_id).SetEnabled(True)


def _enable_wait_primitives(target, enabled):
    for breakpoint_id in WAIT_BREAKPOINT_IDS:
        target.FindBreakpointByID(breakpoint_id).SetEnabled(enabled)


def _plant_calls(process, scope, start, end):
    """Discover direct calls from live code only after the origin is known."""
    existing = {config.get("static") for config in CONFIG.values()
                if config.get("scope") == scope}
    data = _read(process, SLIDE[0] + start, end - start)
    planted = 0
    for offset in range(0, len(data) - 3, 4):
        static_pc = start + offset
        word = int.from_bytes(data[offset:offset + 4], "little")
        control = _decode_control(static_pc, word, start, end)
        if control is None and static_pc in INDIRECT_CALLS:
            mnemonic, target_register, modifier_register = INDIRECT_CALLS[static_pc]
            control = {"mnemonic": mnemonic, "target_register": target_register,
                       "modifier_register": modifier_register, "tail": False}
        if control is None or static_pc in existing:
            continue
        breakpoint = _install(process.GetTarget(), SLIDE[0] + static_pc,
                              "%s_CALL_%x" % (scope.upper(), static_pc),
                              "on_call")
        CONFIG[breakpoint.GetID()].update({
            "scope": scope,
            "static": static_pc,
            "word": word,
            "control": control,
            "hits": 0,
        })
        planted += 1
    _emit("scan", scope=scope, start_static=start, end_static=end,
          bytes_read=len(data), calls_planted=planted)


def _plant_known_range(frame, static_start):
    extent = KNOWN_RANGES.get(static_start)
    if extent is None:
        return
    process = frame.GetThread().GetProcess()
    end, scope, image, symbol = extent
    _emit("function-entry", runtime=SLIDE[0] + static_start,
          static=static_start, image=image, symbol=symbol,
          registers={name: _reg(frame, name)
                     for name in ("x0", "x1", "x2", "x3", "x8", "x9",
                                  "x16", "x17", "x19", "x20")})
    _plant_calls(process, scope, static_start, end)


def _expand_known_boundary(frame, label, static_start):
    """Expand useful orchestration code without tracing metadata hot loops.

    CCS repository methods enumerate every installed module and application.
    Their boundary witnesses still show forward progress, while tracing every
    helper call exhausts the safety budget before startup reaches its next
    externally observable wait.  Deep repository expansion remains opt-in for
    a focused run after a boundary has implicated one specific method.
    """
    if label.startswith("CCS_REPOSITORY_") and not EXPAND_CCS_REPOSITORY:
        _emit("scan-suppressed", label=label, static=static_start,
              reason="ccs-repository-hot-loop")
        return
    _plant_known_range(frame, static_start)


def _plant_dynamic_range(frame, static_start):
    """Expand one runtime-resolved target within a strict byte bound.

    Shared-cache symbols are not available to LLDB's remote target, so an
    unknown implementation has no trustworthy symbol extent. An early return
    is not a safe function boundary because another conditional path may skip
    it. Scan the full configured cap, except for compact linker/ObjC stubs whose
    first unconditional tail transfer is an unambiguous end. Runtime thread
    filtering ensures that only executed callsites expand further.
    """
    if static_start is None:
        return
    process = frame.GetThread().GetProcess()
    data = _read(process, SLIDE[0] + static_start, DYNAMIC_SCAN_BYTES)
    end = static_start + len(data)
    stop_reason = "byte-limit"
    first_return_offset = None
    for offset in range(0, len(data) - 3, 4):
        word = int.from_bytes(data[offset:offset + 4], "little")
        if (first_return_offset is None and
                word in (0xd65f03c0, 0xd65f0bff, 0xd65f0fff)):
            first_return_offset = offset
        is_direct_tail = word >> 26 == 0b000101
        is_indirect_tail = (word & 0xfffffc1f == 0xd61f0000 or
                            word & 0xfffff800 == 0xd71f0800)
        if offset < 24 and (is_direct_tail or is_indirect_tail):
            end = static_start + offset + 4
            stop_reason = "entry-stub-tail"
            break
    scope = "dynamic_%x" % static_start
    _emit("dynamic-range", static=static_start, end_static=end,
          bytes_read=len(data), stop_reason=stop_reason,
          first_return_offset=first_return_offset,
          dynamic_target=TRACE["dynamic_targets"],
          dynamic_target_limit=MAX_DYNAMIC_TARGETS)
    _plant_calls(process, scope, static_start, end)


def _install_target_entry(process, target_runtime, target_static, call_id,
                          force=False):
    target_runtime = _canonical(target_runtime)
    # Shared-cache runtime helpers are extremely hot across the system, and
    # QEMU's gdbstub exposes vCPUs rather than Mach threads.  Only expand new
    # targets in the device-specific framework.  Known methods already have
    # positively installed entry boundaries below.
    if (target_static is None or target_static in KNOWN_RANGES or
            (not force and not 0x259088000 <= target_static < 0x25909d000) or
            target_runtime in ENTRY_POINTS):
        return True
    if force and TRACE["dynamic_targets"] >= MAX_DYNAMIC_TARGETS:
        payload = {
            "call_id": call_id,
            "reason": "dynamic-target-limit",
            "target_runtime": target_runtime,
            "target_static": target_static,
            "dynamic_target_limit": MAX_DYNAMIC_TARGETS,
            "time": time.time(),
        }
        _emit("instrumentation-stop", **payload)
        _write_event("instrumentation-stop.json", payload)
        return False
    breakpoint = _install(process.GetTarget(), target_runtime,
                          "TRACE_TARGET_ENTRY_%d" % call_id,
                          "on_target_entry")
    ENTRY_POINTS[target_runtime] = breakpoint.GetID()
    CONFIG[breakpoint.GetID()].update({
        "entry_target_runtime": target_runtime,
        "entry_target_static": target_static,
        "call_id": call_id,
        "dynamic": force,
    })
    if force:
        TRACE["dynamic_targets"] += 1
    return True


def _arm_objc_dispatch(frame, call_id, receiver, selector):
    """Resolve one selected objc_msgSend stub through its live cache path."""
    target = frame.GetThread().GetProcess().GetTarget()
    group = []
    for static in (0x188000850, 0x1880008c4, 0x188000d24):
        breakpoint = _install(target, SLIDE[0] + static,
                              "OBJC_DISPATCH_%d_%x" % (call_id, static),
                              "on_objc_dispatch")
        OBJC_DISPATCHES[breakpoint.GetID()] = {
            "call_id": call_id,
            "receiver": receiver,
            "selector": selector,
            "group": group,
            "dispatch_static": static,
        }
        group.append(breakpoint.GetID())


def on_objc_dispatch(frame, bp_loc, _dict):
    config = OBJC_DISPATCHES[bp_loc.GetBreakpoint().GetID()]
    if (not _same_thread_fast(frame) or
            _reg(frame, "x0") != config["receiver"] or
            _reg(frame, "x1") != config["selector"]):
        return False
    target_runtime = _canonical(_reg(frame, "x17"))
    target_static = _static(target_runtime)
    # Emit a minimal witness and refresh the deadline before doing any LLDB
    # breakpoint mutation.  If a later operation wedges, the report still has
    # the concrete IMP that was resolved by objc_msgSend during this boot.
    print("OBJC_DISPATCH_HIT call_id=%d dispatch_static=0x%x "
          "target_runtime=0x%x target_static=%s" %
          (config["call_id"], config["dispatch_static"], target_runtime,
           ("0x%x" % target_static if target_static is not None else "none")),
          flush=True)
    _refresh_pending("objc-dispatch-hit")
    process = frame.GetThread().GetProcess()
    for breakpoint_id in config["group"]:
        process.GetTarget().FindBreakpointByID(breakpoint_id).SetEnabled(False)
    _emit("objc-dispatch", call_id=config["call_id"],
          dispatch_static=config["dispatch_static"],
          receiver=config["receiver"], selector=config["selector"],
          target_runtime=target_runtime, target_static=target_static)
    _refresh_pending("objc-dispatch")
    expanded = (not FOLLOW_DYNAMIC or
                _install_target_entry(process, target_runtime, target_static,
                                      config["call_id"], force=True))
    return not expanded


def on_wait_primitive(frame, bp_loc, _dict):
    """Witness a wait and prove whether its syscall/function returns.

    The first version stopped at wait entry, which proves only that a normal
    synchronous IPC was attempted.  Keep running and let probe_watch enforce
    the 30-second deadline; a dynamic LR witness supplies the positive control
    for waits that do return on this same SpringBoard thread.
    """
    if not _same_thread_fast(frame):
        return False
    config = CONFIG[bp_loc.GetBreakpoint().GetID()]
    root_call = TRACE["wait_root_call"]
    if root_call is None and TRACE["wait_controls"] >= MAX_WAIT_CONTROLS:
        return False
    TRACE["wait_sequence"] += 1
    sequence = TRACE["wait_sequence"]
    if root_call is None:
        TRACE["wait_controls"] += 1
    registers = {name: _reg(frame, name)
                 for name in ("x0", "x1", "x2", "x3", "x4", "x5",
                              "x6", "x7", "x8", "x16", "x17")}
    process = frame.GetThread().GetProcess()
    return_runtime = _canonical(_reg(frame, "lr"))
    return_breakpoint = _install(
        process.GetTarget(), return_runtime,
        "WAIT_RETURN_%d" % sequence, "on_wait_return")
    wait_call_id = 0x100000 + sequence
    WAIT_RETURNS[return_breakpoint.GetID()] = {
        "sequence": sequence,
        "call_id": wait_call_id,
        "root_call": root_call,
        "label": config["label"],
        "return_runtime": return_runtime,
        "started": time.time(),
    }
    payload = {
        "call_id": wait_call_id,
        "root_call": root_call,
        "sequence": sequence,
        "reason": "wait-entry",
        "label": config["label"],
        "runtime": config["address"],
        "static": config["address"] - SLIDE[0],
        "pc": _reg(frame, "pc"),
        "lr": _reg(frame, "lr"),
        "return_runtime": return_runtime,
        "registers": registers,
        "backtrace": _backtrace(frame.GetThread(), 40),
        "time": time.time(),
    }
    if config["label"] == "WAIT_MACH_MSG2_TRAP":
        payload["mach_msg2"] = _mach_msg2_arguments(process, registers)
    _emit("wait-entry", **payload)
    _write_event("wait-entry.%d.json" % sequence, payload)
    _write_event("pending.wait.%d.json" % sequence, {
        "call_id": wait_call_id,
        "root_call": root_call,
        "sequence": sequence,
        "selector": TRACE_SELECTOR,
        "time": payload["time"],
        "label": config["label"],
    })
    return STOP_ON_WAIT


def on_wait_return(frame, bp_loc, _dict):
    breakpoint = bp_loc.GetBreakpoint()
    config = WAIT_RETURNS.get(breakpoint.GetID())
    if config is None or not _same_thread_fast(frame):
        return False
    duration = time.time() - config["started"]
    result = _reg(frame, "x0")
    _emit("wait-return", sequence=config["sequence"],
          call_id=config["call_id"], root_call=config["root_call"],
          label=config["label"], return_runtime=config["return_runtime"],
          duration=duration, result=result)
    _remove_event("pending.wait.%d.json" % config["sequence"])
    _write_event("returned.%d.json" % config["call_id"], {
        "call_id": config["call_id"], "sequence": config["sequence"],
        "selector": TRACE_SELECTOR, "time": time.time(),
        "duration": duration, "result": result,
    })
    frame.GetThread().GetProcess().GetTarget().BreakpointDelete(
        breakpoint.GetID())
    WAIT_RETURNS.pop(breakpoint.GetID(), None)
    return False


def on_seeded_wait_return(frame, bp_loc, _dict):
    """Stop when a frozen main thread's already-entered receive completes."""
    if not _same_thread_fast(frame):
        return False
    payload = {
        "reason": "seeded-wait-return",
        "runtime": _reg(frame, "pc"),
        "static": SEEDED_WAIT_RETURN_STATIC,
        "result": _reg(frame, "x0"),
        "thread": frame.GetThread().GetThreadID(),
        "tpidr_el1": _reg(frame, "tpidr_el1"),
        "registers": {name: _reg(frame, name)
                      for name in ("x0", "x1", "x2", "x3", "x4", "x5",
                                   "x6", "x7", "x8", "x16", "x17", "lr")},
        "backtrace": _backtrace(frame.GetThread(), 40),
        "time": time.time(),
    }
    _emit("seeded-wait-return", **payload)
    _write_event("seeded-wait-return.json", payload)
    return STOP_ON_WAIT


def on_target_entry(frame, bp_loc, _dict):
    if not _same_thread(frame):
        return False
    config = CONFIG[bp_loc.GetBreakpoint().GetID()]
    static_start = config["entry_target_static"]
    _emit("target-entry", call_id=config["call_id"],
          target_runtime=config["entry_target_runtime"],
          target_static=static_start, lr=_reg(frame, "lr"),
          registers={name: _reg(frame, name)
                     for name in ("x0", "x1", "x2", "x3", "x8", "x9",
                                  "x16", "x17", "x19", "x20")},
          backtrace=_backtrace(frame.GetThread()))
    _refresh_pending("target-entry")
    _plant_known_range(frame, static_start)
    if static_start not in KNOWN_RANGES and config.get("dynamic"):
        _plant_dynamic_range(frame, static_start)
    # One matching entry is enough to prove the resolved target and expand a
    # known framework function.  Hot helpers may be called hundreds of times
    # while enumerating module metadata; leaving their entry witnesses armed
    # can exhaust the bounded trace before execution reaches a new callsite.
    bp_loc.GetBreakpoint().SetEnabled(False)
    return False


def _arm(frame, anchor_static):
    process = frame.GetThread().GetProcess()
    if TRACE["tpidr_el1"] is not None or _progname(process) != "SpringBoard":
        return False
    TRACE["tpidr_el1"] = _reg(frame, "tpidr_el1")
    TRACE["x18"] = _reg(frame, "x18")
    TRACE["stack_regions"].add((_reg(frame, "sp") or 0) & ~0xfffff)
    TRACE["thread"] = frame.GetThread().GetThreadID()
    TRACE["started"] = time.time()
    _emit("armed", anchor_static=anchor_static, pc=_reg(frame, "pc"),
          sp=_reg(frame, "sp"), backtrace=_backtrace(frame.GetThread()))
    return True


def on_anchor(frame, bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    if _progname(process) != "SpringBoard":
        return False
    if ROOT_LABEL:
        return False
    _arm(frame, 0x1844e484c)
    # Start with bounded same-thread waits as a positive control.  The global
    # breakpoint locations stay dormant throughout early boot, avoiding the
    # severe slowdown caused by filtering every process from reset.
    _enable_wait_primitives(process.GetTarget(), True)
    _plant_calls(process, "query_displays", 0x1844e484c, 0x1844e4d60)
    # The first query is made by _FBSystemShellInitialize.  It has already
    # entered when this anchor fires, but every remaining call site can still
    # be armed during this same boot.  This distinguishes the exact child call
    # that prevents the initializer (and therefore UIApplicationMain) from
    # returning without another one-layer probe.
    # ReadMemory may reject a range that crosses a dyld-cache mapping/page
    # boundary even though both pages are executable.  Keep the two scans
    # page-local and preserve a single logical scope in the report.
    _plant_calls(process, "fb_system_shell", 0x1c4a3bf08, 0x1c4a3c000)
    _plant_calls(process, "fb_system_shell", 0x1c4a3c000, 0x1c4a3c360)
    _emit("mig-return", anchor_static=0x1844e484c,
          backtrace=_backtrace(frame.GetThread()))
    return False


def on_boundary(frame, bp_loc, _dict):
    config = CONFIG[bp_loc.GetBreakpoint().GetID()]
    label = config["label"]
    process = frame.GetThread().GetProcess()
    if label in GLOBAL_PROGRESS_LABELS and _progname(process) == "SpringBoard":
        payload = {
            "label": label,
            "time": time.time(),
            "thread": frame.GetThread().GetThreadID(),
            "tpidr_el1": _reg(frame, "tpidr_el1"),
            "pc": _reg(frame, "pc"),
            "sp": _reg(frame, "sp"),
        }
        _write_event("progress.%s.json" % label, payload)
        print("SPRINGBOARD_GLOBAL_BOUNDARY label=%s thread=0x%x "
              "tpidr_el1=0x%x" %
              (label, payload["thread"], payload["tpidr_el1"]), flush=True)
        if label in SUCCESS_LABELS:
            _write_event("success.%s.json" % label, payload)
            return STOP_ON_SUCCESS
    if PROGRESS_ONLY:
        return False
    if TRACE["tpidr_el1"] is None:
        if ROOT_LABEL and label != ROOT_LABEL:
            return False
        if not _arm(frame, config["address"] - SLIDE[0]):
            return False
        _enable_wait_primitives(process.GetTarget(), True)
    if (label == "CCS_REPOSITORY_INIT_BLOCK" and
            _progname(process) == "SpringBoard" and
            _reg(frame, "x0") in TRACE["contexts"]):
        TRACE["stack_regions"].add((_reg(frame, "sp") or 0) & ~0xfffff)
    if not _same_thread(frame):
        return False
    if label == "SB_INIT_SERVICES_ENTRY":
        _plant_calls(process, "sb_init_services_ef", 0x2244cef28, 0x2244cf000)
    elif label == "SB_INIT_SERVICES_PAGE_F0":
        _plant_calls(process, "sb_init_services_f0", 0x2244cf000, 0x2244cf0d4)
    elif label == "SB_APP_SUPPORT_START_ENTRY":
        _plant_calls(process, "app_support_start", 0x2247c63fc, 0x2247c6428)
    elif label == "SB_APP_SUPPORT_ONCE_ENTRY":
        _plant_calls(process, "app_support_once", 0x2247c6428, 0x2247c6464)
    elif label == "SB_APP_SUPPORT_DELEGATE_INIT":
        _plant_calls(process, "app_support_delegate_init", 0x2247c5628,
                     0x2247c56e4)
    elif label == "SB_APP_SUPPORT_REBUILD_CONTEXT":
        _plant_calls(process, "app_support_rebuild_context", 0x2247c576c,
                     0x2247c57c8)
    elif label == "CADISPLAY_MAIN_ENTRY":
        _plant_calls(process, "cadisplay_main", 0x1843e2e58, 0x1843e2e88)
    elif label == "SB_BEFORE_FB_SYSTEM_SHELL":
        # This call site is in _SBSystemAppMain itself, so it is the reliable
        # main-thread root. Generic CADisplay hits also occur on SpringBoard's
        # application-support worker threads and must not select the trace.
        # FrontBoard's code page is not necessarily resident yet at this
        # pre-call boundary. FB_SYSTEM_SHELL_ENTRY performs discovery after
        # the dyld stub has transferred into the mapped implementation.
        pass
    elif label == "FB_SYSTEM_SHELL_ENTRY":
        # Keep reads page-local: the gdbstub rejects a single ReadMemory that
        # crosses the shared-cache boundary at 0x1c4a3c000.
        _plant_calls(process, "fb_system_shell", 0x1c4a3bd80, 0x1c4a3c000)
        _plant_calls(process, "fb_system_shell", 0x1c4a3c000, 0x1c4a3c360)
    elif config["address"] - SLIDE[0] in KNOWN_RANGES:
        _expand_known_boundary(frame, label, config["address"] - SLIDE[0])
    elif label.startswith(("CCS_", "CORE_SERVICES_")):
        static_start = config["address"] - SLIDE[0]
        _expand_known_boundary(frame, label, static_start)
    _emit("boundary", label=label, pc=_reg(frame, "pc"),
          lr=_reg(frame, "lr"), x0=_reg(frame, "x0"),
          backtrace=_backtrace(frame.GetThread()))
    if label == "QUERY_DISPLAYS_RETURN":
        _plant_calls(process, "ensure_displays", 0x1843e33b8, 0x1843e35a0)
    if label in SUCCESS_LABELS:
        _write_event("success.%s.json" % label,
                     {"label": label, "time": time.time(),
                      "thread": TRACE["thread"]})
    if label.startswith(("CCS_", "CORE_SERVICES_")):
        # The calls inside this function remain armed.  The boundary itself is
        # only a positive-control witness and otherwise turns metadata loops
        # into thousands of redundant debugger stops.
        bp_loc.GetBreakpoint().SetEnabled(False)
    return label in SUCCESS_LABELS and STOP_ON_SUCCESS


def on_call(frame, bp_loc, _dict):
    if not _same_thread(frame) or TRACE["calls"] >= MAX_CALLS:
        return False
    process = frame.GetThread().GetProcess()
    breakpoint = bp_loc.GetBreakpoint()
    config = CONFIG[breakpoint.GetID()]
    static_pc = config["static"]
    control = config["control"]
    config["hits"] += 1
    if config["hits"] >= MAX_SITE_HITS:
        breakpoint.SetEnabled(False)
    mnemonic = control["mnemonic"]
    modifier_register = control.get("modifier_register")
    target_static = control.get("target_static")
    if target_static is not None:
        target_runtime = target_static + SLIDE[0]
    else:
        target_register = control["target_register"]
        target_runtime = _canonical(_reg(frame, target_register))
        target_static = _static(target_runtime)
    TRACE["calls"] += 1
    call_id = TRACE["calls"]
    if static_pc == 0x2590956a8:
        TRACE["contexts"].add(_reg(frame, "x1"))
        _emit("context", role="dispatch_sync_block", pointer=_reg(frame, "x1"),
              repository=_reg(frame, "x0"), call_id=call_id)
    return_address = None if control.get("tail") else config["address"] + 4
    if return_address is not None:
        return_bp = process.GetTarget().BreakpointCreateByAddress(return_address)
        return_bp.SetScriptCallbackFunction(
            "springboard_startup_trace_callbacks.on_return")
        RETURNS[return_bp.GetID()] = {
            "call_id": call_id,
            "callsite_static": static_pc,
            "target_static": target_static,
            "target_runtime": target_runtime,
            "started": time.time(),
            "scope": config["scope"],
        }
        if config["scope"].startswith(
                ("ccs_", "ls_", "dynamic_", "fb_")):
            ACTIVE_CALLS[call_id] = {
                "scope": config["scope"],
                "callsite_static": static_pc,
                "target_static": target_static,
            }
            _refresh_pending("call")
    dynamic_scope = config["scope"].startswith("dynamic_")
    expanded = _install_target_entry(process, target_runtime, target_static,
                                     call_id, force=dynamic_scope)
    if target_static in OBJC_STUBS_TO_RESOLVE:
        TRACE["wait_root_call"] = call_id
        _enable_wait_primitives(process.GetTarget(), True)
        selector = _objc_stub_selector(process, target_runtime, target_static)
        if selector is None:
            _emit("instrumentation-stop", call_id=call_id,
                  reason="cannot-decode-objc-selector",
                  target_runtime=target_runtime, target_static=target_static)
        else:
            _arm_objc_dispatch(frame, call_id, _reg(frame, "x0"), selector)
    _emit("call", call_id=call_id, scope=config["scope"],
          mnemonic=mnemonic, callsite_runtime=config["address"],
          callsite_static=static_pc, target_runtime=target_runtime,
          target_static=target_static, return_runtime=return_address,
          tail=control.get("tail", False),
          site_hit=config["hits"], site_hit_limit=MAX_SITE_HITS,
          registers={name: _reg(frame, name)
                     for name in ("x0", "x1", "x2", "x3", "x8", "x9",
                                  "x16", "x17", "x19", "x20")},
          modifier_register=modifier_register,
          modifier=(_reg(frame, modifier_register)
                    if modifier_register else None),
          backtrace=_backtrace(frame.GetThread()))
    if TRACE["calls"] >= MAX_CALLS:
        for breakpoint_id, candidate in CONFIG.items():
            if candidate.get("scope"):
                process.GetTarget().FindBreakpointByID(breakpoint_id).SetEnabled(False)
        _emit("limit", reason="call-limit", calls=TRACE["calls"])
        _write_event("instrumentation-stop.json", {
            "call_id": call_id, "reason": "call-limit",
            "calls": TRACE["calls"], "time": time.time(),
        })
        return True
    # A failed dynamic expansion is intentionally reported from this exact
    # pre-call boundary. Returning True leaves the inferior stopped here.
    return not expanded


def on_return(frame, bp_loc, _dict):
    breakpoint = bp_loc.GetBreakpoint()
    config = RETURNS.get(breakpoint.GetID())
    if config is None or not _same_thread(frame):
        return False
    _emit("return", call_id=config["call_id"],
          callsite_static=config["callsite_static"],
          target_runtime=config["target_runtime"],
          target_static=config["target_static"],
          duration=time.time() - config["started"],
          result=_reg(frame, "x0"))
    if config["scope"].startswith(
            ("ccs_", "ls_", "dynamic_", "fb_")):
        ACTIVE_CALLS.pop(config["call_id"], None)
        _refresh_pending("return")
        _write_event("returned.%d.json" % config["call_id"],
                     {"call_id": config["call_id"],
                      "selector": TRACE_SELECTOR, "time": time.time()})
    if config["call_id"] == TRACE["wait_root_call"]:
        TRACE["wait_root_call"] = None
        _enable_wait_primitives(frame.GetThread().GetProcess().GetTarget(),
                                False)
    frame.GetThread().GetProcess().GetTarget().BreakpointDelete(breakpoint.GetID())
    RETURNS.pop(breakpoint.GetID(), None)
    return False


def install(debugger, slide):
    SLIDE[0] = slide
    target = debugger.GetSelectedTarget()

    anchor = _install(target, slide + 0x1844e484c,
                      "SPRINGBOARD_QD_MIG_RETURN", "on_anchor")
    del anchor
    for static, label in (
            (0x186dc8bcc, "CORE_SERVICES_LS_CONTEXT_INIT_OPTIONS"),
            (0x186dc8bd0, "CORE_SERVICES_LS_CONTEXT_INIT_COMMON"),
            (0x186dc9668, "CORE_SERVICES_LS_DEFAULT_SERVICE_DOMAIN"),
            (0x186dc96b0, "CORE_SERVICES_LS_DATABASE_CONTEXT_GET"),
            (0x186dc8768, "CORE_SERVICES_LS_APPLICATION_RECORD_INIT_CORE"),
            (0x186dca928, "CORE_SERVICES_LS_APPLICATION_RECORD_INIT_BLOCK"),
            (0x186dd1348, "CORE_SERVICES_LS_APPLICATION_RECORD_INIT_FETCHING_PLACEHOLDER"),
            (0x186dd29e4, "CORE_SERVICES_LS_APPLICATION_RECORD_INIT_ALLOW_PLACEHOLDER"),
            (0x186df21c8, "CORE_SERVICES_LS_COPY_SERVER_STORE"),
            (0x186df2b04, "CORE_SERVICES_LS_COPY_SERVER_STORE_BLOCK"),
            (0x186df15b0, "CORE_SERVICES_LS_READ_CLIENT_GET_SERVER_STORE"),
            (0x186df1e04, "CORE_SERVICES_LS_SERVER_GET_STORE_FOR_CONNECTION"),
            (0x2244cef28, "SB_INIT_SERVICES_ENTRY"),
            (0x2244cf000, "SB_INIT_SERVICES_PAGE_F0"),
            (0x2247c63fc, "SB_APP_SUPPORT_START_ENTRY"),
            (0x224cc6eb0, "SB_APP_SUPPORT_START_COLD"),
            (0x2247c6428, "SB_APP_SUPPORT_ONCE_ENTRY"),
            (0x2247c6460, "SB_APP_SUPPORT_ONCE_RETURN"),
            (0x2247c5628, "SB_APP_SUPPORT_DELEGATE_INIT"),
            (0x2247c56e0, "SB_APP_SUPPORT_DELEGATE_RETURN"),
            (0x2247c576c, "SB_APP_SUPPORT_REBUILD_CONTEXT"),
            (0x2247c57c4, "SB_APP_SUPPORT_REBUILD_RETURN"),
            (0x259097d0c, "CCS_START_SERVICES_ENTRY"),
            (0x259097de0, "CCS_START_SERVICES_BODY"),
            (0x25908a868, "CCS_REMOTE_SHARED_ENTRY"),
            (0x25908a8ac, "CCS_REMOTE_SHARED_ONCE_ENTRY"),
            (0x25908a90c, "CCS_REMOTE_INIT_ENTRY"),
            (0x25908ab00, "CCS_REMOTE_RESUME_ENTRY"),
            (0x2590953c8, "CCS_REPOSITORY_SHARED_ENTRY"),
            (0x259095468, "CCS_REPOSITORY_SHARED_ONCE_ENTRY"),
            (0x259095544, "CCS_REPOSITORY_INIT_ENTRY"),
            (0x2590956d4, "CCS_REPOSITORY_INIT_BLOCK"),
            (0x25909602c, "CCS_REPOSITORY_UPDATE_ALL_ENTRY"),
            (0x259096294, "CCS_REPOSITORY_UPDATE_ALL_FOR_ALL_ENTRY"),
            (0x25909642c, "CCS_REPOSITORY_UPDATE_AVAILABLE_FOR_ALL_ENTRY"),
            (0x259096500, "CCS_REPOSITORY_UPDATE_LOADABLE_FOR_AVAILABLE_ENTRY"),
            (0x25909675c, "CCS_REPOSITORY_UPDATE_LOADABLE_BLOCK"),
            (0x259096768, "CCS_REPOSITORY_MODULE_IDENTIFIERS_ENTRY"),
            (0x2590968d4, "CCS_REPOSITORY_LOAD_ALL_ENTRY"),
            (0x2590969bc, "CCS_REPOSITORY_LOAD_ALL_BLOCK_1"),
            (0x259096a38, "CCS_REPOSITORY_LOAD_ALL_BLOCK_2"),
            (0x259096a80, "CCS_REPOSITORY_LOAD_ALL_BLOCK_3"),
            (0x259096c58, "CCS_REPOSITORY_FILTER_BUNDLE_AVAILABILITY_ENTRY"),
            (0x259096cb8, "CCS_REPOSITORY_FILTER_BUNDLE_AVAILABILITY_BLOCK"),
            (0x259096e60, "CCS_REPOSITORY_UPDATE_INTERESTING_BUNDLES_ENTRY"),
            (0x259096ecc, "CCS_REPOSITORY_ASSOCIATED_BUNDLES_ENTRY"),
            (0x259097038, "CCS_REPOSITORY_HAS_INTERESTING_PROXY_ENTRY"),
            (0x2590970dc, "CCS_REPOSITORY_HAS_INTERESTING_PROXY_BLOCK"),
            (0x259097128, "CCS_REPOSITORY_FILTER_VISIBILITY_ENTRY"),
            (0x259097188, "CCS_REPOSITORY_FILTER_VISIBILITY_BLOCK"),
            (0x2590972ac, "CCS_REPOSITORY_FILTER_GESTALT_ENTRY"),
            (0x259097438, "CCS_REPOSITORY_FILTER_GESTALT_BLOCK_1"),
            (0x259097458, "CCS_REPOSITORY_FILTER_GESTALT_BLOCK_2"),
            (0x25909751c, "CCS_REPOSITORY_UPDATE_GESTALT_QUESTIONS_ENTRY"),
            (0x259097618, "CCS_REPOSITORY_UPDATE_GESTALT_QUESTIONS_BLOCK"),
            (0x259097658, "CCS_REPOSITORY_GESTALT_QUESTIONS_ENTRY"),
            (0x259092828, "CCS_SETTINGS_INIT_ENTRY"),
            (0x1843e2e58, "CADISPLAY_MAIN_ENTRY"),
            (0x1843e2e84, "CADISPLAY_MAIN_RETURN"),
            (0x1844e4d5c, "QUERY_DISPLAYS_RETURN"),
            (0x1843e33b8, "ENSURE_DISPLAYS_AFTER_QUERY"),
            (0x1843e33b0, "ENSURE_DISPLAYS_RETURN"),
            (0x1c4a3bd80, "FB_SYSTEM_SHELL_ENTRY"),
            (0x1c4a3c35c, "FB_SYSTEM_SHELL_RETURN"),
            (0x1c4a15e40, "FB_CREATE_SINGLETON_ENTRY"),
            (0x1c4a15f20, "FB_CREATE_SINGLETON_BLOCK_ENTRY"),
            (0x1c4a158e4, "FB_INIT_OPTIONS_ENTRY"),
            (0x1c4a15c00, "FB_INIT_OPTIONS_PAGE_C0"),
            (0x1c4a15cc0, "FB_INIT_OPTIONS_BLOCK_1"),
            (0x1c4a15d4c, "FB_INIT_OPTIONS_BLOCK_2"),
            (0x224458bf0, "SB_BEFORE_FB_SYSTEM_SHELL"),
            (0x224458bf4, "SB_AFTER_FB_SYSTEM_SHELL"),
            (0x224458c7c, "SB_BEFORE_UIAPPLICATION_MAIN"),
            (0x18490d11c, "UIAPPLICATION_MAIN"),
            (0x2244d0ba4, "SB_ADFL_ENTRY"),
            (0x2244d8cf4, "SB_FINALIZE_ENTRY"),
            (0x2246d37f0, "SB_UPDATE_ENTRY"),
            (0x2246d3adc, "SB_SET_REASON_ENTRY"),
            (0x2243bf920, "SB_SETUPAPP_ENTRY"),
            (0x22433ff18, "SB_ACTIVATE_ENTRY"),
            (0x187f727a4, "SPRINGBOARD_ABORT"),
            (0x237ce21a4, "SPRINGBOARD_EXIT")):
        _install(target, slide + static, label, "on_boundary")

    for static, label in WAIT_PRIMITIVES.items():
        breakpoint = _install(target, slide + static,
                              "WAIT_" + label.upper(),
                              "on_wait_primitive", enabled=False)
        WAIT_BREAKPOINT_IDS.append(breakpoint.GetID())

    print("SPRINGBOARD_TRACE_READY dynamic_calls=%d max_calls=%d "
          "max_site_hits=%d wait_primitives=%d wait_controls=%d "
          "expand_ccs_repository=%d stop_on_success=%d progress_only=%d "
          "stop_on_wait=%d root_label=%s" %
          (int(FOLLOW_DYNAMIC), MAX_CALLS, MAX_SITE_HITS,
           len(WAIT_PRIMITIVES), MAX_WAIT_CONTROLS,
           int(EXPAND_CCS_REPOSITORY), int(STOP_ON_SUCCESS),
           int(PROGRESS_ONLY), int(STOP_ON_WAIT), ROOT_LABEL or "<auto>"))
    _write_event("ready", {"time": time.time(), "selector": TRACE_SELECTOR,
                           "breakpoints": len(CONFIG)})


def resume_install(debugger, slide):
    """Re-arm tracing on a deliberately frozen SpringBoard checkpoint."""
    install(debugger, slide)
    # A progress-only reconnect exists specifically to let an already-running
    # guest advance at full speed until one of the high-level milestones fires.
    # Do not inherit the selected SpringBoard thread as a trace root here: that
    # would enable the global wait breakpoints and turn an otherwise cheap
    # milestone watch into thousands of mach_msg stops.
    if PROGRESS_ONLY:
        print("SPRINGBOARD_RESUME_PROGRESS_ONLY", flush=True)
        _write_event("resumed-progress-only", {
            "time": time.time(),
            "selected_program": _progname(
                debugger.GetSelectedTarget().GetProcess()),
        })
        return
    process = debugger.GetSelectedTarget().GetProcess()
    thread = process.GetSelectedThread()
    frame = thread.GetFrameAtIndex(0) if thread.IsValid() else None
    if SEEDED_TPIDR is not None and SEEDED_STACK_REGION is not None:
        TRACE["tpidr_el1"] = SEEDED_TPIDR
        TRACE["stack_regions"].add(SEEDED_STACK_REGION)
        TRACE["thread"] = None
        TRACE["started"] = time.time()
        _enable_wait_primitives(process.GetTarget(), True)
        _install(process.GetTarget(), slide + SEEDED_WAIT_RETURN_STATIC,
                 "SEEDED_WAIT_RETURN", "on_seeded_wait_return")
        _emit("resumed-seeded", anchor_static=None,
              seeded_tpidr_el1=SEEDED_TPIDR,
              seeded_stack_region=SEEDED_STACK_REGION)
        return
    if (frame is not None and frame.IsValid() and
            _progname(process) == "SpringBoard"):
        static_pc = _static(_reg(frame, "pc"))
        if not _arm(frame, static_pc):
            raise RuntimeError("could not arm frozen SpringBoard checkpoint")
        _enable_wait_primitives(process.GetTarget(), True)
        _emit("resumed", anchor_static=static_pc, pc=_reg(frame, "pc"),
              backtrace=_backtrace(thread))
        return
    # HMP freezes every vCPU and the gdbstub may select an unrelated one when
    # LLDB reconnects.  Every installed high-level boundary can now perform
    # the same exact SpringBoard process/thread check and arm the trace on its
    # next matching hit, preserving the checkpoint without guessing a vCPU.
    print("SPRINGBOARD_RESUME_WAITING_FOR_BOUNDARY", flush=True)
