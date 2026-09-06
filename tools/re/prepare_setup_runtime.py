#!/usr/bin/env python3
"""Prepare a one-shot restore ramdisk for the pre-Buddy R12 disk.

The output is a small, self-contained installer.  It writes only to the
disposable System child mounted by the restore guest: the signed display-policy
page/hash, the input and three diagnostic helpers, their cached launchd entries, and their
loose LaunchDaemon plists.  It does not touch PurpleBuddy, Data preferences, or
activation records.
"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


SERVICE_ROOT = "/System/Library/LaunchDaemons"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_file(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"missing regular file: {path}")


def cdhash(binary: Path) -> str:
    result = subprocess.run(
        ["codesign", "-d", "-vvv", str(binary)], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    for line in result.stderr.splitlines():
        if line.startswith("CDHash="):
            value = line.removeprefix("CDHash=").strip()
            if len(value) == 40 and all(c in "0123456789abcdef" for c in value):
                return value
    raise ValueError(f"no 20-byte CDHash reported for {binary}")


def service(label: str, executable: str, *, keepalive: bool = False) -> dict:
    result = {
        "Label": label,
        "ProgramArguments": [executable],
        "RunAtLoad": True,
        "UserName": "root",
        "ProcessType": "Interactive",
        "StandardOutputPath": "/dev/console",
        "StandardErrorPath": "/dev/console",
    }
    if keepalive:
        result.update(KeepAlive=True, ThrottleInterval=30)
    else:
        result["LaunchOnlyOnce"] = True
    return result


def read_policy_payloads(repo: Path, policy_ramdisk: Path, scratch: Path) -> list[Path]:
    attach = repo / "tools/rootfs/safe_attach.sh"
    mount = Path(subprocess.check_output(
        [str(attach), "attach", str(policy_ramdisk), "--readonly", "--owners", "on"],
        text=True).strip())
    names = [
        "dvm-policy-header", "dvm-policy-before", "dvm-policy-after",
        "dvm-policy-word", "dvm-policy-old-hash", "dvm-policy-new-hash",
        "dvm-policy-offset.sh",
    ]
    try:
        result = []
        for name in names:
            source = mount / "libexec" / name
            require_file(source)
            target = scratch / name
            shutil.copyfile(source, target)
            result.append(target)
        return result
    finally:
        subprocess.run([str(attach), "detach", str(mount)], check=True)


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new output directory")
    parser.add_argument("--ramdisk-base", type=Path,
                        default=Path("/Users/jdolbe1/Downloads/darwin-vm/firmware/ramdisk.dmg"),
                        help="unmodified restore ramdisk to copy")
    parser.add_argument("--base-tc", type=Path,
                        default=Path("/Users/jdolbe1/dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin"))
    parser.add_argument("--launchd-original", type=Path,
                        default=Path("/tmp/dvm/WARM_RUNTIME_STAGE2/launchd-original.plist"))
    parser.add_argument("--launchd-input", type=Path,
                        default=Path("/tmp/dvm/WARM_RUNTIME_STAGE2/launchd-input.plist"))
    parser.add_argument("--input", type=Path,
                        default=Path("/tmp/dvm/warm-input-v5/dvm-input"))
    parser.add_argument("--input-tc", type=Path,
                        default=Path("/tmp/dvm/warm-input-v5/helper.tc"))
    parser.add_argument("--graphics", type=Path,
                        default=Path("/tmp/dvm/GRAPHICS_PROBE6/graphics-probe"))
    parser.add_argument("--graphics-tc", type=Path,
                        default=Path("/tmp/dvm/GRAPHICS_PROBE6/helper.tc"))
    parser.add_argument("--power-rtc", type=Path,
                        default=Path("/tmp/dvm/POWER_PROBE2/power-rtc-probe"))
    parser.add_argument("--power-rtc-tc", type=Path,
                        default=Path("/tmp/dvm/POWER_PROBE2/power-rtc-probe.tc"))
    parser.add_argument("--activation", type=Path,
                        default=Path("/tmp/dvm/activation-probe-environment3-build/activation-probe"))
    parser.add_argument("--activation-tc", type=Path,
                        default=Path("/tmp/dvm/activation-probe-environment3-build/helper.tc"))
    parser.add_argument("--policy-ramdisk", type=Path,
                        default=Path("/tmp/dvm/display-policy2/ramdisk.dmg"))
    parser.add_argument("--policy-tc", type=Path,
                        default=Path("/tmp/dvm/display-policy2/cache.tc"))
    args = parser.parse_args()

    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    inputs = [args.ramdisk_base, args.base_tc, args.launchd_original, args.launchd_input,
              args.input, args.input_tc, args.graphics, args.graphics_tc,
              args.power_rtc, args.power_rtc_tc,
              args.activation, args.activation_tc, args.policy_ramdisk, args.policy_tc]
    try:
        for path in inputs:
            require_file(path)
    except ValueError as error:
        parser.error(str(error))

    original = plistlib.loads(args.launchd_original.read_bytes())
    cached = plistlib.loads(args.launchd_input.read_bytes())
    if not isinstance(original.get("LaunchDaemons"), dict) or not isinstance(cached.get("LaunchDaemons"), dict):
        parser.error("unsupported launchd cache schema")
    input_path = f"{SERVICE_ROOT}/com.apple.dvm-input.plist"
    input_service = plistlib.loads((repo / "tools/input/com.apple.dvm-input.plist").read_bytes())
    if input_path in original["LaunchDaemons"]:
        parser.error("original cache already contains the input service")
    if cached["LaunchDaemons"].get(input_path) != input_service:
        parser.error("cached input service does not match the pinned input plist")
    without_input = dict(cached, LaunchDaemons=dict(cached["LaunchDaemons"]))
    del without_input["LaunchDaemons"][input_path]
    if without_input != original:
        parser.error("cached input artifact changes more than the input service")

    entries = {
        "graphics-probe": (args.graphics, service("com.apple.dvm-graphics-probe", "/usr/local/libexec/dvm-graphics-probe")),
        "power-rtc-probe": (args.power_rtc, service("com.apple.dvm-power-rtc-probe", "/usr/local/libexec/dvm-power-rtc-probe")),
        "activation-probe": (args.activation, service("com.apple.dvm-activation-probe", "/usr/local/libexec/dvm-activation-probe")),
    }
    # A successfully copied executable is not bootable without its own hash
    # in the selected trust cache. Reject stale helper/TC pairs before staging.
    sys.path.insert(0, str(repo / "tools/rootfs"))
    from merge_tc import load as load_tc
    for binary, trust_cache in [
        (args.input, args.input_tc), (args.graphics, args.graphics_tc),
        (args.power_rtc, args.power_rtc_tc),
        (args.activation, args.activation_tc),
    ]:
        if bytes.fromhex(cdhash(binary)) not in {entry[:20] for entry in load_tc(trust_cache)[2]}:
            parser.error(f"{trust_cache} does not trust {binary}")
    cache = dict(cached, LaunchDaemons=dict(cached["LaunchDaemons"]))
    labels = {item.get("Label") for item in cache["LaunchDaemons"].values() if isinstance(item, dict)}
    for name, (_, item) in entries.items():
        path = f"{SERVICE_ROOT}/com.apple.dvm-{name}.plist"
        if path in cache["LaunchDaemons"] or item["Label"] in labels:
            parser.error(f"launchd cache already contains {name}")
        cache["LaunchDaemons"][path] = item
        labels.add(item["Label"])
    derived = plistlib.dumps(cache, fmt=plistlib.FMT_BINARY, sort_keys=False)
    if plistlib.loads(derived) != cache:
        parser.error("derived launchd cache did not round-trip")

    args.output.mkdir()
    scratch = args.output / "policy-payloads"
    scratch.mkdir()
    payloads = read_policy_payloads(repo, args.policy_ramdisk, scratch)
    image = args.output / "ramdisk.dmg"
    shutil.copyfile(args.ramdisk_base, image)
    attach = repo / "tools/rootfs/safe_attach.sh"
    mount = Path(subprocess.check_output([str(attach), "attach", str(image), "--owners", "on"], text=True).strip())
    try:
        dest = mount / "libexec"
        for payload in payloads:
            shutil.copyfile(payload, dest / payload.name)
        shutil.copyfile(args.launchd_original, dest / "dvm-launchd-before.plist")
        (dest / "dvm-launchd-cache.plist").write_bytes(derived)
        shutil.copyfile(args.input, dest / "dvm-input")
        shutil.copyfile(repo / "tools/input/com.apple.dvm-input.plist", dest / "dvm-input-service.plist")
        for name, (binary, item) in entries.items():
            shutil.copyfile(binary, dest / f"dvm-{name}")
            (dest / f"dvm-{name}-service.plist").write_bytes(plistlib.dumps(item, fmt=plistlib.FMT_BINARY))
        shutil.copyfile(Path(__file__).with_name("install_setup_runtime.sh"), dest / "dvm-setup-runtime-install.sh")
        for path in dest.glob("dvm-*"):
            if path.name.endswith((".sh", "-install.sh")) or path.name in {"dvm-input", *[f"dvm-{name}" for name in entries]}:
                path.chmod(0o755)
        subprocess.run(["sync"], check=True)
    finally:
        subprocess.run([str(attach), "detach", str(mount)], check=True)

    hashes = {"ramdisk": sha256(image), "launchd_original": sha256(args.launchd_original),
              "launchd_derived": hashlib.sha256(derived).hexdigest(),
              "policy_ramdisk": sha256(args.policy_ramdisk)}
    for name, (binary, _) in {"input": (args.input, input_service), **entries}.items():
        hashes[name] = sha256(binary)
        hashes[f"{name}_cdhash"] = cdhash(binary)
    for payload in payloads:
        hashes[f"policy/{payload.name}"] = sha256(payload)
    (args.output / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")
    (args.output / "launchd.plist").write_bytes(derived)

    subprocess.run(["python3", str(repo / "tools/rootfs/merge_tc.py"), str(args.output / "system.tc"),
                    str(args.base_tc), str(args.input_tc), str(args.graphics_tc), str(args.power_rtc_tc),
                    str(args.activation_tc), str(args.policy_tc)], check=True)
    print(f"prepared {args.output}")


if __name__ == "__main__":
    main()
