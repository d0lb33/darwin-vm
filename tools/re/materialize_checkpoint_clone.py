#!/usr/bin/env python3
"""Materialize a sealed qcow2 chain using a private APFS clone of its raw base.

qemu-img convert -b copies only the top layer and requires identical backing
contents (docs/tools/qemu-img.rst). Apply layers bottom-up, committing each
temporary overlay exclusively to the new raw clone, never the source chain.
Requires macOS clonefile support; no fallback to a space-consuming full copy.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--qemu-img', type=Path, required=True)
    a = ap.parse_args()
    source = a.source.resolve(strict=True)
    qimg = str(a.qemu_img.resolve(strict=True))
    if source.stat().st_mode & 0o222:
        ap.error('source must be a sealed read-only checkpoint disk')
    chain = json.loads(subprocess.check_output(
        [qimg, 'info', '--backing-chain', '--output=json', str(source)], text=True))
    if chain[-1]['format'] != 'raw' or any(x['format'] != 'qcow2' for x in chain[:-1]):
        ap.error('requires qcow2 overlays over a raw base')
    a.out.mkdir(parents=True, exist_ok=False)
    output = a.out.resolve() / 'disk.dmg'
    base = Path(chain[-1]['filename']).resolve(strict=True)
    started = time.monotonic()
    subprocess.run(['cp', '-c', str(base), str(output)], check=True)
    if output.samefile(base):
        raise RuntimeError('clone unexpectedly aliases source inode')
    output.chmod(0o600)
    steps = []
    for index, layer in enumerate(reversed(chain[:-1])):
        overlay = output.parent / f'layer-{index}.qcow2'
        subprocess.run([qimg, 'convert', '-f', 'qcow2', '-O', 'qcow2',
                        '-b', str(output), '-F', 'raw', layer['filename'],
                        str(overlay)], check=True)
        info = json.loads(subprocess.check_output(
            [qimg, 'info', '--output=json', str(overlay)], text=True))
        backing = Path(info['full-backing-filename']).resolve(strict=True)
        if backing != output or info.get('backing-filename-format') != 'raw':
            raise RuntimeError('refusing commit outside private raw clone')
        subprocess.run([qimg, 'commit', '-f', 'qcow2', str(overlay)], check=True)
        steps.append(dict(source=layer['filename'], converted_bytes=info['actual-size']))
        overlay.unlink()
        print(json.dumps(steps[-1]), flush=True)
    # Compare logical contents, not allocation: APFS clone allocation differs.
    subprocess.run([qimg, 'compare', '-f', 'qcow2', '-F', 'raw',
                    str(source), str(output)], check=True)
    output.chmod(0o444)
    report = dict(source=str(source), image=str(output), source_chain=chain,
                  steps=steps, logical_contents_equal=True,
                  seconds=time.monotonic()-started)
    (output.parent/'materialize-report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(image=str(output), logical_contents_equal=True)), flush=True)


if __name__ == '__main__':
    main()
