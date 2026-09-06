#!/usr/bin/env python3
"""Stage reviewed, equal-length shared-cache edits and their page signatures.

The JSON spec names a dyld_shared_cache_arm64e subcache and a list of edits:
{cache_name, purpose, edits: [{offset, before, after}]}. Offsets may be hex
strings. Every preimage and original SHA-256 page hash must match. This does
not choose a patch or modify the source cache; the restore guest applies the
reviewed payload only to a disposable System disk child.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

from prepare_display_policy import PAGE_SIZE, parse_code_directory


def reviewed_edits(spec, source):
    """Validate the whole edit set before constructing any changed page."""
    if not re.fullmatch(r'dyld_shared_cache_arm64e(?:\.\d{2})?', spec['cache_name']):
        raise ValueError('unsupported cache filename')
    if not isinstance(spec.get('purpose'), str) or not spec['purpose'].strip():
        raise ValueError('a reviewed purpose is required')
    edits = []
    for item in spec['edits']:
        offset = int(item['offset'], 0) if isinstance(item['offset'], str) else item['offset']
        before, after = bytes.fromhex(item['before']), bytes.fromhex(item['after'])
        if type(offset) is not int or offset < 4096 or not before or len(before) != len(after):
            raise ValueError('edits require equal nonempty lengths after the cache header')
        if len(before) > PAGE_SIZE or offset // PAGE_SIZE != (offset + len(before) - 1) // PAGE_SIZE:
            raise ValueError('each edit must fit in one code page')
        source.seek(offset)
        if source.read(len(before)) != before:
            raise ValueError(f'preimage mismatch at {offset:#x}')
        if before == after:
            raise ValueError('no-op edit')
        edits.append((offset, before, after))
    if not edits or len(edits) > 32:
        raise ValueError('requires 1..32 reviewed edits')
    edits.sort()
    for left, right in zip(edits, edits[1:]):
        if left[0] + len(left[1]) > right[0]:
            raise ValueError('overlapping edits')
    return edits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', type=Path)
    parser.add_argument('spec', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--tc', required=True, type=Path)
    parser.add_argument('--helper-name')
    parser.add_argument('--helper-before', type=Path)
    parser.add_argument('--helper-after', type=Path)
    parser.add_argument('--helper-tc', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output must be new')
    spec = json.loads(args.spec.read_text())
    helper = (args.helper_name, args.helper_before, args.helper_after, args.helper_tc)
    if any(helper) and (not all(helper) or not re.fullmatch(r'dvm-[a-z0-9-]+', args.helper_name)):
        parser.error('helper replacement requires a safe dvm-* name, before, after, and tc')
    repo = Path(__file__).resolve().parents[2]
    if all(helper):
        sys.path.insert(0, str(repo / 'tools/rootfs'))
        from merge_tc import load as load_tc
        subprocess.run(['codesign', '--verify', '--strict', str(args.helper_after)], check=True)
        signature = subprocess.check_output(['codesign', '-d', '--verbose=4', str(args.helper_after)], stderr=subprocess.STDOUT, text=True)
        helper_hash = re.search(r'^CDHash=([0-9a-f]{40})$', signature, re.M).group(1)
        if bytes.fromhex(helper_hash) not in {entry[:20] for entry in load_tc(args.helper_tc)[2]}:
            parser.error('helper trust cache does not cover replacement binary')
        spec['helper'] = dict(name=args.helper_name, cdhash=helper_hash,
            before_sha256=hashlib.sha256(args.helper_before.read_bytes()).hexdigest(),
            after_sha256=hashlib.sha256(args.helper_after.read_bytes()).hexdigest())
    payloads = []
    with args.cache.open('rb') as source:
        header = source.read(4096)
        if len(header) != 4096 or not header.startswith(b'dyld'):
            parser.error('unsupported shared-cache header')
        signature_offset, signature_size = struct.unpack_from('<QQ', header, 0x28)
        if signature_offset + signature_size > args.cache.stat().st_size:
            parser.error('signature extends beyond cache')
        source.seek(signature_offset)
        signature = source.read(signature_size)
        try:
            edits = reviewed_edits(spec, source)
            cd, cd_offset, _ = parse_code_directory(signature, 0,
                required_code_end=max(offset + len(before) for offset, before, _ in edits))
            for page_index in sorted({offset // PAGE_SIZE for offset, _, _ in edits}):
                source.seek(page_index * PAGE_SIZE)
                original = source.read(PAGE_SIZE)
                if len(original) != PAGE_SIZE:
                    raise ValueError('truncated code page')
                _, _, slot = parse_code_directory(signature, page_index)
                old_hash = bytes(cd[slot:slot + 32])
                if hashlib.sha256(original).digest() != old_hash:
                    raise ValueError(f'original page {page_index} fails signature hash')
                changed = bytearray(original)
                for offset, before, after in edits:
                    if offset // PAGE_SIZE == page_index:
                        within = offset % PAGE_SIZE
                        changed[within:within + len(before)] = after
                new_hash = hashlib.sha256(changed).digest()
                cd[slot:slot + 32] = new_hash
                payloads.append((signature_offset + cd_offset + slot, old_hash, new_hash))
        except (KeyError, TypeError, ValueError) as error:
            parser.error(str(error))
    payloads = edits + payloads
    args.output.mkdir()
    image = args.output / 'ramdisk.dmg'
    shutil.copyfile(repo / 'firmware/ramdisk.dmg', image)
    wrapper = repo / 'tools/rootfs/safe_attach.sh'
    mount = Path(subprocess.check_output([str(wrapper), 'attach', str(image), '--owners', 'on'], text=True).strip())
    try:
        dest = mount / 'libexec'
        (dest / 'dvm-cache-header').write_bytes(header)
        lines = [f'cache_name={spec["cache_name"]}', f'patch_count={len(payloads)}']
        if all(helper):
            lines.append(f'helper_name={args.helper_name}')
            shutil.copyfile(args.helper_before, dest / 'dvm-helper-before')
            shutil.copyfile(args.helper_after, dest / 'dvm-helper-after')
        for index, (offset, before, after) in enumerate(payloads):
            (dest / f'dvm-cache-before-{index}').write_bytes(before)
            (dest / f'dvm-cache-after-{index}').write_bytes(after)
            lines += [f'offset_{index}={offset}', f'length_{index}={len(before)}']
        (dest / 'dvm-cache-offsets.sh').write_text('\n'.join(lines) + '\n')
        shutil.copyfile(Path(__file__).with_name('install_guarded_cache_patch.sh'), dest / 'dvm-cache-patch-install.sh')
        subprocess.run(['sync'], check=True)
    finally:
        subprocess.run([str(wrapper), 'detach', str(mount)], check=True)
    cdhash = hashlib.sha256(cd).hexdigest()[:40]
    (args.output / 'hashes.txt').write_text(cdhash + '\n')
    subprocess.run(['python3', str(repo / 'build_tc.py'), str(args.output / 'hashes.txt'), str(args.output / 'cache.tc')], check=True)
    trust_caches = [str(args.tc), str(args.output / 'cache.tc')]
    if all(helper):
        trust_caches.append(str(args.helper_tc))
    subprocess.run(['python3', str(repo / 'tools/rootfs/merge_tc.py'), str(args.output / 'system.tc'), *trust_caches], check=True)
    spec.update(source_cache=str(args.cache.resolve()), code_directory_hash=cdhash,
                signature_edits=[dict(offset=hex(offset), before=before.hex(), after=after.hex())
                                 for offset, before, after in payloads[len(edits):]])
    (args.output / 'patch.json').write_text(json.dumps(spec, indent=2) + '\n')
    print(args.output)


if __name__ == '__main__':
    main()
