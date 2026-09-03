"""Regression tests for the small kcdata decoder used on guest RAM dumps."""
import importlib.util
import io
import os
import struct
import tempfile
import unittest


MODULE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "oskcdata.py")
SPEC = importlib.util.spec_from_file_location("oskcdata", MODULE)
OSKCDATA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OSKCDATA)


def item(kind, payload=b"", flags=0):
    """Make one aligned kcdata item."""
    out = struct.pack("<IIQ", kind, len(payload), flags) + payload
    return out + b"\0" * ((-len(payload)) & 3)


class OSKcdataTests(unittest.TestCase):
    def test_walk_uses_xnu_buffer_end_not_ffffffff(self):
        # KCDATA_TYPE_BUFFER_END is 0xf19158ed in osfmk/kern/kcdata.h.  Bytes
        # after it must not be considered part of this OS_REASON buffer.
        blob = b"".join((
            item(0x53A20900, flags=0x80),
            item(0x36, struct.pack("<i", 149)),
            item(0xF19158ED),
            item(0x1002, b"must not be rendered\0"),
        ))
        self.assertEqual(
            [kind for kind, _size, _payload in OSKCDATA.walk(blob, 0)],
            [0x53A20900, 0x36, 0xF19158ED],
        )

    def test_render_pid_and_procname_without_inventing_user_description(self):
        blob = b"".join((
            item(0x53A20900, flags=0x80),
            item(0x36, struct.pack("<i", 149)),
            item(0x37, b"exc handler\0"),
            item(0xF19158ED),
            item(0x1002, b"must not be rendered\0"),
        ))
        with tempfile.NamedTemporaryFile() as f:
            f.write(blob)
            f.flush()
            out = io.StringIO()
            self.assertTrue(OSKCDATA.render(f.name, 0, "OS_REASON", out))
        text = out.getvalue()
        self.assertIn("KCDATA_TYPE_PID", text)
        self.assertIn("149", text)
        self.assertIn("KCDATA_TYPE_PROCNAME", text)
        self.assertIn("'exc handler'", text)
        self.assertNotIn("must not be rendered", text)
        self.assertNotIn("EXIT_REASON_USER_DESC", text)

    def test_current_crashinfo_process_name_type_is_809(self):
        blob = b"".join((
            item(0xDEADF157, flags=0x80),
            item(0x805, struct.pack("<i", 149)),
            item(0x809, b"SpringBoard\0"),
            item(0xF19158ED),
        ))
        with tempfile.NamedTemporaryFile() as f:
            f.write(blob)
            f.flush()
            out = io.StringIO()
            self.assertTrue(OSKCDATA.render(f.name, 0, "CRASHINFO", out))
        text = out.getvalue()
        self.assertIn("TASK_CRASHINFO_PID", text)
        self.assertIn("149", text)
        self.assertIn("TASK_CRASHINFO_PROC_NAME", text)
        self.assertIn("'SpringBoard'", text)
        self.assertNotIn(0x847, OSKCDATA.ITEM)


if __name__ == "__main__":
    unittest.main()
