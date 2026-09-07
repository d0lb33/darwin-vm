"""Shared render allocation + lease tests using real host Metal and scattered RAM.

Native wait reports below are synthetic. No guest/DCP acceptance is claimed.
"""
import json
import os
import select
import struct
import subprocess
import time
import unittest
from managed_pages import read_resource, LENGTH, PAGE
import test_managed_driver as managed_fixture
from test_driver_host import BUILD


class SharedRenderTests(unittest.TestCase):
    setUp = managed_fixture.ManagedTests.setUp
    tearDown = managed_fixture.ManagedTests.tearDown
    rpc = managed_fixture.ManagedTests.rpc
    manifest = managed_fixture.ManagedTests.manifest
    canaries = managed_fixture.ManagedTests.canaries

    def test_owned_surface_frontend(self):
        env={k:v for k,v in os.environ.items() if not k.startswith('DVM_DRIVER_')}
        env.update(DVM_DRIVER_PRESENT_RAM=str(self.out/'shared-ram.bin'),
                   DVM_DRIVER_MANAGED_RAM=str(self.out/'managed-ram.bin'),
                   DVM_DRIVER_MANAGED_PAGES=str(self.out/'managed-pages.bin'))
        result=subprocess.run([str(BUILD/'test_owned_surface_frontend')],env=env,capture_output=True,text=True,timeout=15)
        (BUILD/'owned-surface-frontend.stderr').write_text(result.stderr)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('PASS owned IOSurface frontend:',result.stderr)

    def create(self, **changes):
        fields = dict(width=1179, height=2556, row=4864, format=80, usage=5, bytes=LENGTH)
        fields.update(changes)
        return self.rpc('sharedRenderCreate', **fields)

    def render(self, handle, color, **changes):
        fields = dict(commands=[dict(kind='render', target=handle, load=2, store=1,
                                     clear=color, operations=[])], uploads=[], readbacks=[])
        fields.update(changes)
        return self.rpc('renderSubmit', **fields)

    def retire(self, handle, current_epoch, **changes):
        fields = dict(handle=handle, epoch=current_epoch, swap=19, waitMode=1, waitResult=0)
        fields.update(changes)
        return self.rpc('sharedRenderRetire', **fields)

    def test_scattered_pages_gpu_writes_reuse_and_retirement(self):
        before = read_resource(self.out)
        handle = self.create()['handle']
        self.assertEqual(read_resource(self.out), before, 'allocation must preserve CPU bytes')
        self.assertFalse(self.create()['ok'], 'one mapping owner')
        self.assertFalse(self.render(handle, [1, 0, 0, 1])['ok'])
        self.assertFalse(self.rpc('read', texture=handle)['ok'], 'no copied readback path')
        self.assertFalse(self.rpc('sharedRenderAcquire', handle=handle, epoch=2)['ok'])
        for epoch, color in enumerate(([0, 0, 1, 1], [0, 1, 0, 1], [1, 0, 0, 1]), 1):
            self.assertTrue(self.rpc('sharedRenderAcquire', handle=handle, epoch=epoch)['ok'])
            self.assertFalse(self.rpc('sharedRenderSeal', handle=handle, epoch=epoch)['ok'])
            self.assertFalse(self.retire(handle, epoch)['ok'])
            self.assertFalse(self.rpc('release', handle=handle)['ok'])
            reply = self.render(handle, color)
            self.assertTrue(reply['ok'], reply)
            self.assertEqual(reply['status'], 4)
            # A frame may require several native command buffers before seal.
            load = dict(kind='render', target=handle, load=1, store=1, clear=[0, 0, 0, 0], operations=[])
            self.assertTrue(self.render(handle, color, commands=[load])['ok'])
            seal = self.rpc('sharedRenderSeal', handle=handle, epoch=epoch)
            self.assertTrue(seal['ok'], seal)
            self.assertEqual(seal['completedWrites'], 2)
            self.assertFalse(self.render(handle, [0, 0, 0, 0])['ok'])
            self.assertFalse(self.rpc('sharedRenderAcquire', handle=handle, epoch=epoch+1)['ok'])
            self.assertFalse(self.rpc('release', handle=handle)['ok'])
            for bad in (dict(epoch=epoch+1), dict(waitMode=0), dict(waitResult=1), dict(swap=-1)):
                self.assertFalse(self.retire(handle, epoch, **bad)['ok'])
            self.assertTrue(self.retire(handle, epoch)['ok'])
            self.assertFalse(self.retire(handle, epoch)['ok'])
        # Verification outside the batch, through original scattered file pages.
        pixels = read_resource(self.out)
        row = bytes([0, 0, 255, 255])*1179
        for y in range(2556):
            self.assertEqual(pixels[y*4864:y*4864+len(row)], row)
            self.assertEqual(pixels[y*4864+len(row):(y+1)*4864], before[y*4864+len(row):(y+1)*4864])
        self.assertEqual(pixels[4864*2556:], before[4864*2556:])
        self.assertTrue(self.rpc('release', handle=handle)['ok'])
        self.assertEqual(self.rpc('stats')['live']['resourceBytes'], 0)
        self.assertFalse(self.rpc('sharedRenderAcquire', handle=handle, epoch=4)['ok'])
        new = self.create()['handle']
        self.assertGreater(new, handle)
        self.assertEqual(read_resource(self.out), pixels)
        self.assertTrue(self.rpc('release', handle=new)['ok'])

    def test_failed_later_pass_cannot_modify_shared_pixels(self):
        before = read_resource(self.out)
        handle = self.create()['handle']
        self.assertTrue(self.rpc('sharedRenderAcquire', handle=handle, epoch=1)['ok'])
        first = dict(kind='render', target=handle, load=2, store=1, clear=[1, 0, 0, 1], operations=[])
        bad = dict(first, operations=[['pipeline', 99999]])
        self.assertFalse(self.render(handle, [], commands=[first, bad])['ok'])
        self.assertEqual(self.rpc('stats')['submissions'], 0)
        self.assertEqual(read_resource(self.out), before)
        self.assertFalse(self.rpc('sharedRenderSeal', handle=handle, epoch=1)['ok'])
        self.assertTrue(self.render(handle, [1, 0, 0, 1])['ok'])
        self.assertTrue(self.rpc('sharedRenderSeal', handle=handle, epoch=1)['ok'])
        self.assertTrue(self.retire(handle, 1)['ok'])
        self.assertTrue(self.rpc('release', handle=handle)['ok'])

    def test_mapping_and_descriptor_rejection(self):
        for bad in (dict(width=64), dict(height=2557), dict(row=4716), dict(format=70),
                    dict(usage=7), dict(bytes=LENGTH-PAGE), dict(width=1179.0)):
            self.assertFalse(self.create(**bad)['ok'])
        variants = [b'foreign-session!'+self.record[16:], self.record[:-8]]
        for offset in (self.offsets[1], 1, 0x300000000):
            record = bytearray(self.record)
            struct.pack_into('<Q', record, 32, offset)
            variants.append(record)
        for record in variants:
            self.manifest(record)
            self.assertFalse(self.create()['ok'])
        self.assertEqual(self.rpc('stats')['live']['resourceBytes'], 0)
        self.manifest(self.record)
        handle = self.create()['handle']
        self.assertTrue(self.rpc('release', handle=handle)['ok'])

    def test_cpu_updates_are_visible_without_uploads(self):
        handle = self.create()['handle']
        for epoch, pixel in enumerate((bytes([3, 11, 79, 255]), bytes([99, 23, 5, 255])), 1):
            # The caller owns the CPU phase only after previous retirement.
            with (self.out/'managed-ram.bin').open('r+b') as ram:
                ram.seek(self.offsets[0])
                ram.write(pixel)
            self.assertTrue(self.rpc('sharedRenderAcquire', handle=handle, epoch=epoch)['ok'])
            load = dict(kind='render', target=handle, load=1, store=1, clear=[0, 0, 0, 0], operations=[])
            reply = self.render(handle, [], commands=[load])
            self.assertTrue(reply['ok'], reply)
            self.assertEqual(read_resource(self.out)[:4], pixel)
            self.assertTrue(self.rpc('sharedRenderSeal', handle=handle, epoch=epoch)['ok'])
            self.assertTrue(self.retire(handle, epoch)['ok'])
        self.assertTrue(self.rpc('release', handle=handle)['ok'])

    def test_gpu_completion_waits_for_delayed_event(self):
        # Fixture sets a 150 ms native GPU-event hold for this test name.
        handle = self.create()['handle']
        self.assertTrue(self.rpc('sharedRenderAcquire', handle=handle, epoch=1)['ok'])
        first = read_resource(self.out)[:16]
        self.seq += 1
        raw = json.dumps(dict(seq=self.seq, op='renderSubmit', commands=[dict(kind='render',
            target=handle, load=2, store=1, clear=[1, 0, 0, 1], operations=[])], uploads=[], readbacks=[])).encode()
        started = time.monotonic()
        self.p.stdin.write(struct.pack('<I', len(raw))+raw)
        self.p.stdin.flush()
        self.assertEqual(select.select([self.p.stdout], [], [], .03)[0], [])
        self.assertEqual(read_resource(self.out)[:16], first)
        length, = struct.unpack('<I', self.p.stdout.read(4))
        reply = json.loads(self.p.stdout.read(length))
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(reply['status'], 4)
        self.assertGreaterEqual(time.monotonic()-started, .14)
        self.assertEqual(read_resource(self.out)[:4], bytes([0, 0, 255, 255]))
        self.assertTrue(self.rpc('sharedRenderSeal', handle=handle, epoch=1)['ok'])
        self.assertTrue(self.retire(handle, 1)['ok'])
        self.assertTrue(self.rpc('release', handle=handle)['ok'])


if __name__ == '__main__':
    unittest.main()
