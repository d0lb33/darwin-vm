"""State-machine checks for dvm-hid cancellation and completion boundaries."""
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "tools/input/dvm_hid.c"


class HelperLifecycleTests(unittest.TestCase):
    def test_cancel_orders_releases_before_new_input(self):
        source = f'''\
#define main dvm_hid_program_main
#include "{HELPER}"
#undef main
#include <assert.h>
#undef assert
#define assert(value) do {{ if (!(value)) {{ fprintf(stderr, "assertion %s at %d\\n", #value, __LINE__); return 1; }} }} while (0)

static struct record record(unsigned epoch, unsigned seq, char kind,
                            unsigned a, unsigned b) {{
    return (struct record){{.epoch = epoch, .seq = seq, .kind = kind,
                           .a = a, .b = b}};
}}
int main(void) {{
    struct record down = record(1, 1, 'D', 100, 200);
    struct record cancel = record(2, 2, 'C', 0, 0);
    struct record next = record(2, 3, 'D', 300, 400);
    state = 'R'; epoch = 1; timebase.numer = timebase.denom = 1;

    /* A queued down is discarded by C, with no synthetic completion. */
    assert(handle(&down) == 'Q' && op_len == 1);
    assert(handle(&(struct record){{.epoch=1,.seq=2,.kind='C'}}) == 'Q');
    assert(op_len == 0 && !held && !touch_down);

    /* A down already in the worker is balanced before new-epoch input. */
    dispatching = true;
    active_op = (struct op){{.epoch=1,.seq=1,.generation=op_generation,
                             .kind='T',.down=true,.x=.1,.y=.2}};
    assert(handle(&cancel) == 'Q');
    assert(touch_release_needed);
    assert(op_len == 0);
    dispatching = false; touch_down = true; touch_x = .1; touch_y = .2;
    assert(schedule_releases(&cancel) && op_len == 1 && !ops[op_head].down);
    assert(handle(&next) == 'Q' && op_len == 2);
    assert(!ops[op_head].down && ops[(op_head + 1) % OP_QUEUE].down);

    /* Repeated C replaces, rather than loses or duplicates, the release. */
    assert(handle(&(struct record){{.epoch=2,.seq=4,.kind='C'}}) == 'Q');
    assert(op_len == 1 && !ops[op_head].down);

    /* Home held in an old epoch is likewise released before a new record. */
    op_head = op_len = 0; touch_down = touch_release_needed = touch_release_enqueued = false;
    button_down = button_bit(0x40);
    assert(handle(&(struct record){{.epoch=2,.seq=5,.kind='C'}}) == 'Q');
    assert(op_len == 1 && ops[op_head].kind == 'B' && !ops[op_head].down &&
           ops[op_head].usage == 0x40);

    /* Orphans and a wheel during a drag are rejected, never accepted with
       an undrainable DVMI2D requirement. */
    held = false;
    assert(handle(&(struct record){{.epoch=2,.seq=6,.kind='M',.a=1,.b=1}}) == 'E');
    held = true;
    assert(handle(&(struct record){{.epoch=2,.seq=7,.kind='W',.a=1,.b=1,.c=1}}) == 'E');
    held = false; op_len = OP_QUEUE;
    assert(handle(&(struct record){{.epoch=2,.seq=8,.kind='D',.a=1,.b=1}}) == 'F');
    assert(!held && op_len <= 3);
    assert(op_len == 1 && ops[op_head].kind == 'B' && !ops[op_head].down);
    return 0;
}}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lifecycle.c"
            binary = Path(directory) / "lifecycle"
            path.write_text(source)
            subprocess.run(["clang", "-Wall", "-Wextra", "-Werror", str(path), "-o", str(binary)],
                           check=True, capture_output=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
