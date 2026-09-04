"""Host-only tests for exact checkpoint restore command rewriting."""

import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

from checkpoint_common import (  # noqa: E402
    parse_migration_status, process_argv_env, qcow2_backing_chain, restore_argv,
    sptm_panic_message, serial_hex_clock_bounds, verify_backing_chain,
)
from restore_checkpoint import activate_paused_disks  # noqa: E402


class CheckpointCommandTests(unittest.TestCase):
    def test_paused_activation_handles_events_without_resuming_cpu(self):
        stream = mock.MagicMock()
        stream.readline.side_effect = [
            b'{"QMP": {}}\n', b'{"event": "STOP"}\n',
            b'{"return": {}, "id": 0}\n',
            b'{"return": {}, "id": 1}\n',
        ]
        with mock.patch("restore_checkpoint.socket.socket") as socket_factory:
            connection = socket_factory.return_value.__enter__.return_value
            connection.makefile.return_value.__enter__.return_value = stream
            activate_paused_disks(Path("/tmp/test.qmp"))
        commands = [json.loads(call.args[0]) for call in stream.write.call_args_list]
        self.assertEqual([command["execute"] for command in commands],
                         ["qmp_capabilities", "blockdev-set-active"])
        self.assertEqual(commands[1]["arguments"], {"active": True})

    def test_paused_activation_error_is_not_ignored(self):
        stream = mock.MagicMock()
        stream.readline.side_effect = [
            b'{"QMP": {}}\n', b'{"return": {}, "id": 0}\n',
            b'{"error": {"desc": "locked"}, "id": 1}\n',
        ]
        with mock.patch("restore_checkpoint.socket.socket") as socket_factory:
            connection = socket_factory.return_value.__enter__.return_value
            connection.makefile.return_value.__enter__.return_value = stream
            with self.assertRaisesRegex(RuntimeError, "locked"):
                activate_paused_disks(Path("/tmp/test.qmp"))

    def test_qemu_11_migration_status_format(self):
        self.assertEqual(parse_migration_status("Status:\t\tcompleted\n"),
                         "completed")
        self.assertEqual(parse_migration_status("Migration status: failed\n"),
                         "failed")

    def test_serial_firmware_clock_bounds(self):
        text = ("noise\n0x0000000000000010 event\n"
                "0x000000000000001f another\n")
        self.assertEqual(serial_hex_clock_bounds(text), (0x10, 0x1f))

    def test_sptm_branch_to_self_message_is_decoded(self):
        hmp = mock.Mock()
        hmp.command.side_effect = [
            "0xfffffff000001000: b #0xfffffff000001000",
            "0x2000: 0x62 0x61 0x64 0x00 0x00",
        ]
        self.assertEqual(
            sptm_panic_message(
                hmp, "PC=fffffff000001000 X03=0000000000002000"
            ),
            "bad",
        )

    def test_backing_chain_is_hash_pinned_and_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            top = root / "top.qcow2"
            base = root / "base.raw"
            top.write_bytes(b"overlay")
            base.write_bytes(b"base")
            qemu_info = """[
              {"filename": "%s", "format": "qcow2", "virtual-size": 64},
              {"filename": "%s", "format": "raw", "virtual-size": 64}
            ]""" % (top, base)
            with mock.patch("checkpoint_common.subprocess.check_output",
                            return_value=qemu_info):
                chain = qcow2_backing_chain(Path("qemu-img"), top)
            self.assertEqual([entry["path"] for entry in chain],
                             [str(top.resolve()), str(base.resolve())])
            verify_backing_chain(chain)
            base.write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "resized|hash mismatch"):
                verify_backing_chain(chain)

    def test_restore_rewrites_only_host_resources_and_ans_disk(self):
        source = [
            "/work/qemu-system-aarch64", "-M", "darwin",
            "-args", "rootdev=disk1s1 serial=3 -v",
            "-monitor", "unix:/tmp/dvm/source.sock,server,nowait",
            "-chardev", "socket,id=probe_uart,path=/tmp/dvm/source.uart,"
                        "server=on,wait=off,logfile=/tmp/source.log",
            "-serial", "chardev:probe_uart",
            "-drive", "if=none,id=ans,file=/images/source.qcow2,format=qcow2",
            "-gdb", "tcp::1234", "-pidfile", "/tmp/source.pid", "-S",
            "-qmp", "unix:/tmp/source.qmp,server=on,wait=off",
        ]
        result = restore_argv(
            source, Path("/images/source.qcow2"), Path("/restore/disk.qcow2"),
            Path("/tmp/dvm/restore.sock"), Path("/restore/serial.log"),
            Path("/tmp/dvm/restore.uart"), Path("/checkpoint/vmstate.bin"),
            2345,
        )
        self.assertIn("rootdev=disk1s1 serial=3 -v", result)
        self.assertIn("unix:/tmp/dvm/restore.sock,server,nowait", result)
        self.assertIn("if=none,id=ans,file=/restore/disk.qcow2,format=qcow2", result)
        self.assertIn("file:/checkpoint/vmstate.bin", result)
        self.assertIn("tcp::2345", result)
        self.assertNotIn("tcp::1234", result)
        self.assertNotIn("/tmp/source.pid", result)
        self.assertNotIn("unix:/tmp/source.qmp,server=on,wait=off", result)
        self.assertEqual(result.count("-S"), 1)

    @unittest.skipUnless(sys.platform == "darwin", "kern.procargs2 is macOS-only")
    def test_process_argv_is_not_split_on_spaces(self):
        import subprocess

        proc = subprocess.Popen(["/bin/sleep", "30"])
        try:
            argv, _ = process_argv_env(proc.pid)
            self.assertEqual(argv, ["/bin/sleep", "30"])
        finally:
            proc.terminate()
            proc.wait()


if __name__ == "__main__":
    unittest.main()
