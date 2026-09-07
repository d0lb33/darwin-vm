"""Host regressions for the backboardd session generation/ownership state machine.

These exercise the peer's bookkeeping without a VM or a Metal worker: the
generation allocator, single-claim package staging, retirement accounting and
the reuse stop that follows an unacknowledged process replacement.
"""
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_peer
from session_peer import SessionPeer


def peer(directory):
    """A SessionPeer with only the fields its control ops use."""
    p = SessionPeer.__new__(SessionPeer)
    p.out = Path(directory)
    p.ram = bytearray(0x1000)
    p.header = bytes(range(16))
    p.ownership_failure = None
    p.tombstones = []
    (p.out/'managed-pages.bin.imports').mkdir()
    (p.out/'serial.log').write_bytes(b'')
    (p.out/'stderr.log').write_bytes(b'')
    p.jobs = p.out/'session-jobs'
    p.jobs.mkdir()
    p.records = []
    p.audit_seen = 0
    p.generation = 0
    p.generations = []
    p.staged = None
    p.staged_history = []
    p.quarantine = []
    p.worker_generations = []
    p.reuse = True
    p.reuse_stopped = None
    p.replacements = []

    def replace(worker=None):
        record = dict(generation=p.generation, pid=1000+len(p.replacements),
                      path='worker', replaced_monotonic=__import__('time').monotonic())
        p.worker_generations.append(record)
        p.replacements.append(record)
        return record
    p.replace_worker = replace
    return p


def pages(p, resource):
    (p.registry_directory()/('%016x.pages' % resource)).write_bytes(b'\0'*64)


def retire(p, generation=1, imports=0, done=0, relinquished=True, revision=1):
    p.quiesce(dict(generation=generation, imports=[]))
    p.retired(dict(generation=generation, imports=imports, importsRetired=done,
                   relinquished=relinquished, revision=revision, requests=1))


def package(p, job=1, revision=2, payload=b'\xcf\xfa\xed\xfe'+b'\0'*64):
    record = dict(job=job, revision=revision, sha256='x'*64, bytes=len(payload), claimed_by=None, loaded=None)
    p.staged = dict(record, payload=payload, info=b'i', resources=b'r')
    p.staged_history.append(record)
    return record


class SessionControlLayout(unittest.TestCase):
    def test_python_offsets_match_the_c_header(self):
        text = (Path(__file__).resolve().with_name('session_control.h')).read_text()
        base = int(re.search(r'#define DVM_SESSION_BASE\s+(0x[0-9a-fA-F]+)', text)[1], 16)
        self.assertEqual(base, session_peer.SESSION_BASE)
        pairs = dict(re.findall(r'#define (DVM_SESSION_\w+)\s+\(DVM_SESSION_BASE \+ (0x[0-9a-fA-F]+)u\)', text))
        names = dict(DVM_SESSION_MAGIC_OFFSET=session_peer.MAGIC_OFFSET,
                     DVM_SESSION_RETIRE_GENERATION=session_peer.RETIRE_GENERATION,
                     DVM_SESSION_GUEST_GENERATION=session_peer.GUEST_GENERATION,
                     DVM_SESSION_GUEST_PID=session_peer.GUEST_PID,
                     DVM_SESSION_GUEST_REVISION=session_peer.GUEST_REVISION,
                     DVM_SESSION_GUEST_STATE=session_peer.GUEST_STATE,
                     DVM_SESSION_GUEST_REQUESTS=session_peer.GUEST_REQUESTS,
                     DVM_SESSION_GUEST_IMPORTS=session_peer.GUEST_IMPORTS,
                     DVM_SESSION_GUEST_IMPORTS_DONE=session_peer.GUEST_IMPORTS_DONE)
        for name, value in names.items():
            self.assertEqual(base+int(pairs[name], 16), value, name)
        self.assertEqual(int(re.search(r'#define DVM_SESSION_MAGIC UINT64_C\((0x[0-9a-fA-F]+)\)', text)[1], 16),
                         session_peer.SESSION_MAGIC)
        # Every word must stay inside the gap the existing consumers leave
        # between the present configuration and the audit slots.
        end = int(re.search(r'#define DVM_SESSION_END\s+\(DVM_SESSION_BASE \+ (0x[0-9a-fA-F]+)u\)', text)[1], 16)+base
        self.assertGreaterEqual(base, 0x210)
        self.assertLessEqual(end, 0x1000)


class Generations(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.p = peer(self.directory.name)
        self.addCleanup(self.directory.cleanup)

    def test_hello_allocates_increasing_generations_after_retirement(self):
        self.assertEqual(self.p.hello(dict(pid=73)), dict(generation=1))
        retire(self.p, imports=2, done=2)
        self.assertEqual(self.p.hello(dict(pid=91)), dict(generation=2))
        self.assertTrue(self.p.reuse)
        self.assertEqual(self.p.quarantine, [])

    def test_hello_rejects_a_bad_pid(self):
        for pid in (0, -1, 'seven', None):
            with self.assertRaises(ValueError):
                self.p.hello(dict(pid=pid))

    def test_replacement_without_retirement_quarantines_and_stops_reuse(self):
        self.p.hello(dict(pid=73))
        self.p.hello(dict(pid=91))
        self.assertTrue(self.p.generations[0]['unclean_exit'])
        self.assertFalse(self.p.reuse)
        self.assertEqual(len(self.p.quarantine), 1)
        self.assertIsNone(self.p.quarantine[0]['count'])
        with self.assertRaises(ValueError):
            self.p.request_retire()
        package(self.p)
        self.p.staged = None
        with self.assertRaises(ValueError):
            self.p.stage(Path('/nonexistent'), 3)

    def test_retirement_quarantines_unretired_imports(self):
        self.p.hello(dict(pid=73))
        retire(self.p, imports=3, done=1)
        entry = self.p.generations[0]
        self.assertEqual((entry['imports'], entry['imports_retired'], entry['imports_quarantined']), (3, 1, 2))
        self.assertEqual(self.p.quarantine[0]['count'], 2)
        self.assertTrue(self.p.reuse, 'a clean retirement that quarantines pages still allows reuse')

    def test_retirement_ownership_is_checked(self):
        self.p.hello(dict(pid=73))
        self.p.quiesce(dict(generation=1, imports=[]))
        with self.assertRaises(ValueError):
            self.p.retired(dict(generation=2, imports=0, importsRetired=0))


class Staging(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.p = peer(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.p.hello(dict(pid=73))

    def test_idle_without_a_package(self):
        self.assertEqual(self.p.next_package(), dict(action='idle'))

    def test_package_is_claimed_exactly_once(self):
        package(self.p)
        first = self.p.next_package()
        self.assertEqual(first['action'], 'stage')
        self.assertEqual(first['revision'], 2)
        self.assertEqual(self.p.generations[0]['staged_job'], 1)
        self.assertEqual(self.p.next_package(), dict(action='idle'))
        retire(self.p, revision=2)
        self.p.hello(dict(pid=91))
        self.assertEqual(self.p.next_package(), dict(action='idle'),
                         'a claimed package is never handed to a second generation')

    def test_fetch_ownership_and_bounds(self):
        package(self.p)
        with self.assertRaises(ValueError):
            self.p.fetch(dict(job=1, offset=0))  # not claimed yet
        self.p.next_package()
        self.assertIn('data', self.p.fetch(dict(job=1, offset=0)))
        for bad in (dict(job=2, offset=0), dict(job=1, offset=-1), dict(job=1, offset=10**9), dict(job=1, offset='0')):
            with self.assertRaises(ValueError):
                self.p.fetch(bad)

    def test_staged_result_is_recorded_for_its_generation(self):
        package(self.p)
        self.p.next_package()
        self.p.staged_result(dict(generation=1, job=1, loaded=False, reason='bundle-load', revision=1, pid=73))
        result = self.p.generations[0]['staged_result']
        self.assertEqual((result['loaded'], result['reason']), (False, 'bundle-load'))
        self.assertFalse(self.p.staged_history[0]['loaded'])
        with self.assertRaises(ValueError):
            self.p.staged_result(dict(generation=9, job=1, loaded=True))

    def test_package_request_before_hello_is_refused(self):
        empty = peer(tempfile.mkdtemp())
        with self.assertRaises(ValueError):
            empty.next_package()


class Retirement(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.p = peer(self.directory.name)
        self.addCleanup(self.directory.cleanup)

    def test_only_the_live_generation_retires_and_only_once(self):
        self.p.hello(dict(pid=73))
        published = {}
        self.p.publish = lambda offset, value: published.__setitem__(offset, value)
        self.p.request_retire()
        self.assertEqual(published[session_peer.RETIRE_GENERATION], 1)
        with self.assertRaises(ValueError):
            self.p.request_retire()
        with self.assertRaises(ValueError):
            self.p.request_retire(99)

    def test_generation_records_are_written(self):
        self.p.hello(dict(pid=73))
        retire(self.p, imports=1, done=0)
        written = json.loads((self.p.jobs/'generation-1-retired.json').read_text())
        self.assertEqual(written['imports_quarantined'], 1)


if __name__ == '__main__':
    unittest.main()


class Quiescence(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.p = peer(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.p.hello(dict(pid=73))

    def test_quiesce_replaces_the_worker_before_any_tombstone(self):
        pages(self.p, 1)
        pages(self.p, 2)
        reply = self.p.quiesce(dict(generation=1, imports=[1, 2]))
        self.assertEqual(reply['tombstones'], [1, 2])
        self.assertEqual(len(self.p.replacements), 1,
                         'the aliasing worker must be gone before a tombstone authorizes an unpin')
        self.assertLess(self.p.replacements[0]['replaced_monotonic'],
                        self.p.tombstones[0]['monotonic'])

    def test_tombstone_record_matches_the_registry_abi(self):
        pages(self.p, 7)
        self.p.quiesce(dict(generation=1, imports=[7]))
        record = (self.p.registry_directory()/('%016x.retired' % 7)).read_bytes()
        self.assertEqual(len(record), 32)
        self.assertEqual(record[:16], self.p.header)
        self.assertEqual(struct.unpack_from('<QQ', record, 16), (7, session_peer.SURFACE_RETIRED_MAGIC))

    def test_tombstone_refuses_an_unregistered_or_invalid_resource(self):
        self.assertFalse(self.p.write_tombstone(3), 'no page manifest means no proven registration')
        for bad in (0, -1, 1 << 33, '4', None):
            with self.assertRaises(ValueError):
                self.p.write_tombstone(bad)

    def test_quiesce_is_single_shot_and_validates_identities(self):
        self.p.quiesce(dict(generation=1, imports=[]))
        with self.assertRaises(ValueError):
            self.p.quiesce(dict(generation=1, imports=[]))
        second = peer(tempfile.mkdtemp())
        second.hello(dict(pid=73))
        for bad in ([0], [1 << 33], ['1'], list(range(1, 100))):
            with self.assertRaises(ValueError):
                second.quiesce(dict(generation=1, imports=bad))
        with self.assertRaises(ValueError):
            second.quiesce(dict(generation=2, imports=[]))

    def test_retirement_requires_the_quiescence_handshake(self):
        with self.assertRaises(ValueError):
            self.p.retired(dict(generation=1, imports=0, importsRetired=0, relinquished=True))

    def test_retirement_without_relinquish_fails_ownership(self):
        retire(self.p, relinquished=False)
        self.assertFalse(self.p.reuse)
        self.assertIn('relinquish', self.p.ownership_failure)

    def test_successor_after_a_clean_retirement_is_accepted(self):
        retire(self.p)
        self.assertEqual(self.p.hello(dict(pid=91)), dict(generation=2))
        self.assertIsNone(self.p.ownership_failure)
        self.assertTrue(self.p.reuse)

    def test_successor_must_talk_to_the_backend_the_quiescence_created(self):
        retire(self.p)
        # A backend that is not the one this boundary created could still hold
        # the previous generation's guest-assigned handles.
        self.p.worker_generations.append(dict(generation=1, pid=4242, path='stale'))
        self.p.hello(dict(pid=91))
        self.assertFalse(self.p.reuse)
        self.assertIn('quiesced through a worker replacement', self.p.ownership_failure)

    def test_a_failed_worker_replacement_authorizes_no_tombstone(self):
        pages(self.p, 5)

        def broken(worker=None):
            raise RuntimeError('worker bootstrap')
        self.p.replace_worker = broken
        with self.assertRaises(RuntimeError):
            self.p.quiesce(dict(generation=1, imports=[5]))
        self.assertFalse((self.p.registry_directory()/('%016x.retired' % 5)).exists())
        self.assertIsNone(self.p.generations[0].get('quiesced_monotonic'))
        with self.assertRaises(ValueError):
            self.p.retired(dict(generation=1, imports=1, importsRetired=0, relinquished=True))

    def test_unacknowledged_replacement_is_an_ownership_failure(self):
        self.p.hello(dict(pid=91))
        self.assertIn('acknowledged retirement', self.p.ownership_failure)
        self.assertFalse(self.p.reuse)


class RevisionContract(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.p = peer(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.p.hello(dict(pid=73))
        package(self.p, job=1, revision=2)
        self.p.staged_history[0]['sha256'] = 'a'*64
        self.p.staged['sha256'] = 'a'*64
        self.p.next_package()
        self.entry = self.p.generations[0]

    def report(self, **overrides):
        base = dict(generation=1, job=1, loaded=True, revision=2, sha256='a'*64,
                    **{'class': 'DVMRevision2Device'})
        base.update(overrides)
        self.p.staged_result(base)
        struct.pack_into('<Q', self.p.ram, session_peer.GUEST_REVISION, overrides.get('live', 2))
        return self.p.revision_contract(self.entry)

    def test_matching_revision_digest_and_class_passes(self):
        ok, detail = self.report()
        self.assertTrue(ok, detail)

    def test_fallback_registration_fails(self):
        ok, detail = self.report(loaded=False, reason='bundle-load', revision=1, sha256='',
                                 **{'class': 'DVMDevice'})
        self.assertFalse(ok)
        self.assertIn('not loaded', detail)

    def test_wrong_revision_digest_or_class_fails(self):
        for overrides, marker in ((dict(revision=3), 'not the staged'),
                                  (dict(sha256='b'*64), 'digest'),
                                  ({'class': 'DVMDevice'}, 'does not carry')):
            p = peer(tempfile.mkdtemp())
            p.hello(dict(pid=73))
            package(p, job=1, revision=2)
            p.staged_history[0]['sha256'] = 'a'*64
            p.staged['sha256'] = 'a'*64
            p.next_package()
            report = dict(generation=1, job=1, loaded=True, revision=2, sha256='a'*64,
                          **{'class': 'DVMRevision2Device'})
            report.update(overrides)
            p.staged_result(report)
            struct.pack_into('<Q', p.ram, session_peer.GUEST_REVISION, 2)
            ok, detail = p.revision_contract(p.generations[0])
            self.assertFalse(ok, overrides)
            self.assertIn(marker, detail)

    def test_live_guest_revision_must_agree(self):
        self.p.staged_result(dict(generation=1, job=1, loaded=True, revision=2, sha256='a'*64,
                                  **{'class': 'DVMRevision2Device'}))
        struct.pack_into('<Q', self.p.ram, session_peer.GUEST_REVISION, 1)
        ok, detail = self.p.revision_contract(self.entry)
        self.assertFalse(ok)
        self.assertIn('live guest revision', detail)

    def test_missing_result_fails(self):
        ok, detail = self.p.revision_contract(self.entry)
        self.assertFalse(ok)
        self.assertIn('never reported', detail)

    def test_generation_without_a_package_passes(self):
        ok, detail = self.p.revision_contract(dict(staged_job=None))
        self.assertTrue(ok)
