#!/usr/bin/env python3
"""Read the Mac's internal-ISA guest policy inputs without changing settings.

These inputs do not reveal the live GXF register or prove HVF capability.
Only the property-presence flag is retained from the device-tree query.
"""
import argparse
import ctypes
import json
from pathlib import Path
import plistlib
import subprocess
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    if sys.platform != 'darwin':
        ap.error('This read-only host inspection requires macOS')
    with a.out.open('x') as output:
        result = {}
        try:
            lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
            read_config = lib.csr_get_active_config
            read_config.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
            read_config.restype = ctypes.c_int
            value = ctypes.c_uint32()
            result['csr_get_active_config_return'] = read_config(ctypes.byref(value))
            if result['csr_get_active_config_return'] == 0:
                result['csr_active_config'] = hex(value.value)
                # apple-oss-distributions/xnu bsd/sys/csr.h: bits 4 and 12.
                result['research_or_internal_bits'] = hex(value.value & 0x1010)
            response = subprocess.run(
                ['ioreg', '-p', 'IODeviceTree', '-n', 'product', '-r', '-a'],
                capture_output=True, check=True, timeout=5)
            items = plistlib.loads(response.stdout)
            result['product_nodes'] = len(items)
            result['internal_isa_vm_allowed_property_present'] = [
                'internal-isa-vm-allowed' in item for item in items]
        except Exception as exc:
            result['error'] = str(exc)
        json.dump(result, output, indent=2)
    print(json.dumps(result, indent=2))
    return int('error' in result or result.get('csr_get_active_config_return') != 0)


if __name__ == '__main__':
    raise SystemExit(main())
