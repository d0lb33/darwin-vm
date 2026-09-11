#!/usr/bin/env python3
"""Retain a legacy checkpoint's /tmp qcow2 beside its durable manifest.

Use ``--backing`` when the top overlay currently names an earlier /tmp
checkpoint disk that has already been retained elsewhere.  The replacement
backing must be byte-identical to the recorded old backing before the header
is changed.  Guest-visible sectors are never rewritten.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from checkpoint_common import (atomic_json, qcow2_backing_chain,
                               replace_drive_file, sha256,
                               verify_backing_chain)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--backing", type=Path,
                        help="durable replacement for the current immediate backing")
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("format") != "darwin-vm-external-checkpoint-v1":
        raise RuntimeError("unsupported checkpoint manifest")
    if sha256(Path(manifest["vmstate"]["path"])) != manifest["vmstate"]["sha256"]:
        raise RuntimeError("VM-state hash mismatch")
    old_chain = manifest["disk"]["backing_chain"]
    if args.backing:
        # An earlier chain member may itself already have been relocated and
        # rebased. Verify this checkpoint's top and every unaffected ancestor;
        # the immediate backing is checked against its relocation receipt below.
        verify_backing_chain([old_chain[0], *old_chain[2:]])
    else:
        verify_backing_chain(old_chain)
    source = Path(manifest["disk"]["path"]).resolve()
    source_sha256_before = sha256(source)
    if source.stat().st_mode & 0o222:
        raise RuntimeError("source checkpoint disk is writable")
    destination = manifest_path.parent / "disk.qcow2"
    if destination.exists() and not os.path.samefile(source, destination):
        raise RuntimeError(f"destination already exists: {destination}")
    if not destination.exists():
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)

    qemu = Path(manifest["qemu_argv"][0]).resolve()
    qemu_img = qemu.with_name("qemu-img")
    replacement = args.backing.resolve() if args.backing else None
    if replacement:
        if len(old_chain) < 2:
            raise RuntimeError("top disk has no immediate backing to replace")
        old_backing = Path(old_chain[1]["path"])
        if not replacement.is_file():
            raise RuntimeError("replacement backing is missing")
        expected_old_hash = old_chain[1]["sha256"]
        replacement_hash = sha256(replacement)
        if replacement_hash != expected_old_hash:
            receipt_path = replacement.parent / "evidence" / "disk-relocation.json"
            receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
            if not (
                Path(receipt.get("source", "")).resolve() == old_backing.resolve() and
                Path(receipt.get("destination", "")).resolve() == replacement and
                receipt.get("source_sha256_before") == expected_old_hash and
                receipt.get("disk_sha256") == replacement_hash
            ):
                raise RuntimeError(
                    "replacement backing is neither byte-identical nor tied to "
                    "the recorded old backing by a relocation receipt"
                )
        destination.chmod(0o644)
        try:
            subprocess.run([
                str(qemu_img), "rebase", "-u", "-f", "qcow2", "-F", "qcow2",
                "-b", str(replacement), str(destination),
            ], check=True)
        finally:
            destination.chmod(0o444)
    else:
        destination.chmod(0o444)

    check = subprocess.run(
        [str(qemu_img), "check", "-f", "qcow2", str(destination)],
        capture_output=True, text=True, check=True,
    ).stdout
    chain = qcow2_backing_chain(qemu_img, destination)
    source_argv = manifest.get("source_qemu_argv", manifest["qemu_argv"])
    replay_argv = []
    index = 0
    while index < len(manifest["qemu_argv"]):
        if manifest["qemu_argv"][index] == "-drive" and index + 1 < len(manifest["qemu_argv"]):
            replay_argv += [
                "-drive",
                replace_drive_file(manifest["qemu_argv"][index + 1], source,
                                   destination.resolve()),
            ]
            index += 2
        else:
            replay_argv.append(manifest["qemu_argv"][index])
            index += 1
    manifest["source_qemu_argv"] = source_argv
    manifest["qemu_argv"] = replay_argv
    manifest["disk"] = {
        "path": str(destination.resolve()),
        "bytes": destination.stat().st_size,
        "sha256": chain[0]["sha256"],
        "mode": oct(destination.stat().st_mode & 0o777),
        "qemu_img_check": manifest["disk"]["qemu_img_check"],
        "backing_chain": chain,
    }
    serial = Path(manifest.get("source_serial_log", {}).get("path", ""))
    if serial.is_file() and serial.parent != manifest_path.parent / "evidence":
        retained_serial = manifest_path.parent / "evidence" / "source-serial.log"
        shutil.copy2(serial, retained_serial)
        manifest["source_serial_log"] = {
            "path": str(retained_serial), "bytes": retained_serial.stat().st_size,
            "sha256": sha256(retained_serial),
        }
    record = {
        "format": "darwin-vm-checkpoint-disk-relocation-v1",
        "unix": time.time(), "source": str(source),
        "source_sha256_before": source_sha256_before,
        "destination": str(destination.resolve()),
        "replacement_backing": str(replacement) if replacement else None,
        "disk_sha256": chain[0]["sha256"], "qemu_img_check": check,
    }
    atomic_json(manifest_path.parent / "evidence" / "disk-relocation.json", record)
    atomic_json(manifest_path, manifest)
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
