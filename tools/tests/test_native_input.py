"""Host-side checks for the DVMI2 native input transport.

The guest helper's record parser and ACK format are exercised in its
``--validate`` mode, which never loads HID frameworks.  The QEMU device's
queue policy is exercised by compiling its record/queue routines with the
QEMU-specific calls stubbed out, the same technique test_uart_fifo.py uses for
the UART FIFO fix that this transport depends on.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'tools/input/dvm_hid.c'
DEVICE = ROOT / 'qemu-sptm/hw/arm/darwin_input.c'
HEADER = ROOT / 'qemu-sptm/include/xnu/darwin_input.h'


def build(source_text, directory, name):
    c = Path(directory) / f'{name}.c'
    binary = Path(directory) / name
    c.write_text(source_text)
    subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    str(c), '-o', str(binary)], check=True, capture_output=True)
    return binary


class HelperParserTests(unittest.TestCase):
    def test_validate_mode_acks_and_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'dvm-hid'
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', str(HELPER), '-o', str(binary)],
                           check=True, capture_output=True)
            result = subprocess.run([str(binary), '--validate'], input=(
                'DVMI2 1 1 P 0 0 0 0\n'
                'DVMI2 1 2 D 100 200 0 1788600000000\n'
                'kernel noise DVMI2 1 3 M 32767 32767 0 0\n'
                'DVMI2 1 4 U 100 200 0 0 trailing\n'
                'DVMI2 1 5 X 0 0 0 0\n'
                'DVMI2 1 6 M 32768 0 0 0\n'
                'DVMI2 1 7 B 64 1 0 0\n'
                'DVMI2 1 8 B 64 2 0 0\n'
                'DVMI2 1 9 C 0 0 0 0\n'
                'DVMI2 1 10 C 1 0 0 0\n'
                'DVMINPUT1 11 T 1 1 1\n'
                'DVMI2 1 13 W 100 200 -2 0\n'
                'DVMI2 1 14 W 100 200 0 0\n'
                'DVMI2 1 15 D 100 200 5 0\n'
                'DVMI2 2 12 P 0 0 0'), text=True, capture_output=True, check=True)
            acks = re.findall(r'DVMI2A (\d+) (\d+) ([A-Z]) ([A-Z]) \d+', result.stderr)
            self.assertEqual([(e, s, c) for e, s, c, _ in acks],
                             [('1', '1', 'Q'), ('1', '2', 'Q'), ('1', '3', 'Q'),
                              ('1', '7', 'Q'), ('1', '9', 'Q'), ('1', '13', 'Q')])
            self.assertTrue(all(state == 'R' for *_, state in acks))
            self.assertEqual(result.stderr.count('DVM_HID_REJECT'), 7)
            self.assertIn('DVM_HID_EOF', result.stderr)


class DeviceQueueTests(unittest.TestCase):
    def harness(self):
        source = DEVICE.read_text()
        header = HEADER.read_text()
        header = header[header.index('#define DARWIN_INPUT_QUEUE'):header.index('void darwin_input_init')]
        header = header.replace('DeviceState *uart;', 'void *uart;').replace('QEMUTimer *timer;', 'void *timer;')
        defines = source[source.index('#define ACK_TIMEOUT_NS'):
                         source.index('static void darwin_input_pump(DarwinInputState *s);')]
        body = defines + source[source.index('static void darwin_input_pump(DarwinInputState *s);'):
                      source.index('/* ---------------- guest replies ---------------- */')]
        replies = source[source.index('static bool needs_dispatch('):source.index('/* ---------------- timer ---------------- */')]
        return r'''
#include <assert.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define NANOSECONDS_PER_SECOND 1000000000LL
#define SCALE_MS 1000000LL
#define SCALE_US 1000LL
#define CLAMP(v, lo, hi) ((v) < (lo) ? (lo) : (v) > (hi) ? (hi) : (v))
#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define QEMU_CLOCK_VIRTUAL 0
#define QEMU_CLOCK_REALTIME 1
static int64_t fake_clock = 1000 * SCALE_MS;
static int64_t qemu_clock_get_ns(int type) { (void)type; return fake_clock; }
static void timer_mod(void *t, int64_t when) { (void)t; (void)when; }
/* The fake FIFO has a replenishable byte budget and records the stream. */
static int fifo_space = 8192;
static char wire_log[8192];
static size_t wire_len;
static int exynos4210_uart_inject(void *dev, const uint8_t *buf, int len) {
    (void)dev;
    if (len > fifo_space) len = fifo_space;
    memcpy(wire_log + wire_len, buf, len);
    wire_len += len;
    fifo_space -= len;
    return len;
}
''' + header + body + replies + r'''
static DarwinInputState s;
static int count(const char *needle) {
    int n = 0;
    for (const char *p = wire_log; (p = strstr(p, needle)); p += strlen(needle)) n++;
    return n;
}
int main(void) {
    memset(&s, 0, sizeof(s));
    s.enabled = true; s.epoch = 1; s.next_seq = 1;
    /* Nothing is sent before the guest announces itself. */
    darwin_input_abs(&s, true, 100); darwin_input_abs(&s, false, 200);
    darwin_input_button(&s, true); darwin_input_sync(&s);
    assert(s.q_len == 0 && s.c_overflow == 1 && !s.contact_sent);
    darwin_input_button(&s, false); darwin_input_sync(&s);
    ready_line(&s, "R 87 0");
    assert(s.guest_state == 'R' && s.guest_pid == 87);
    /* Cancel from the initial epoch precedes everything else. */
    s.cancel_pending = true;
    darwin_input_pump(&s);
    assert(count("DVMI2 1 1 C 0 0 ") == 1);
    ack_line(&s, "1 1 Q R 5");
    /* A tap: down, coalesced motion, up.  Motion never hides the edges. */
    darwin_input_button(&s, true); darwin_input_sync(&s);
    for (int i = 0; i < 50; i++) { darwin_input_abs(&s, true, 100 + i); darwin_input_sync(&s); }
    darwin_input_button(&s, false); darwin_input_sync(&s);
    /* Window: with four unacknowledged records the release waits its turn. */
    assert(s.inflight_n == DARWIN_INPUT_WINDOW && s.q_len == 2);
    assert(count(" U 149 200 ") == 0);
    while (s.inflight_n) {
        char line[64]; snprintf(line, sizeof(line), "1 %u S R 7", s.inflight[0].seq);
        if (needs_dispatch(s.inflight[0].kind)) {
            char done[64]; snprintf(done, sizeof(done), "1 %u S 7", s.inflight[0].seq);
            dispatch_line(&s, done);
        }
        ack_line(&s, line);
    }
    assert(count(" D 100 200 ") == 1);
    assert(count(" U 149 200 ") == 1);
    assert(s.c_coalesced > 40 && s.q_len == 0);
    for (int i = 0; i < 3; i++) {
        darwin_input_button(&s, true); darwin_input_sync(&s);
        darwin_input_button(&s, false); darwin_input_sync(&s);
    }
    assert(s.inflight_n == DARWIN_INPUT_WINDOW && s.q_len == 2);
    while (s.inflight_n) {
        char line[64]; snprintf(line, sizeof(line), "1 %u S R 7", s.inflight[0].seq);
        if (needs_dispatch(s.inflight[0].kind)) {
            char done[64]; snprintf(done, sizeof(done), "1 %u S 7", s.inflight[0].seq);
            dispatch_line(&s, done);
        }
        ack_line(&s, line);
    }
    assert(s.q_len == 0 && s.c_acked >= 10 && s.c_ack_failed == 0);
    /* Partial FIFO writes keep byte order across ticks. */
    fifo_space = 3;
    darwin_input_button(&s, true); darwin_input_sync(&s);
    for (int i = 0; i < 40; i++) { fifo_space = 3; darwin_input_pump(&s); }
    assert(strstr(wire_log, " D 149 200 "));
    fifo_space = 8192;
    /* Guest restart: new epoch, held contact is cancelled, nothing replays. */
    uint32_t epoch = s.epoch;
    ready_line(&s, "I 215 0");
    assert(s.epoch == epoch + 1 && s.c_guest_restarts == 1 && !s.contact_sent && s.q_len == 0);
    darwin_input_pump(&s);
    char cancel[32]; snprintf(cancel, sizeof(cancel), "DVMI2 %u ", s.epoch);
    assert(strstr(wire_log, cancel));
    /* A release arriving after the restart is a harmless no-op record. */
    darwin_input_button(&s, false); darwin_input_sync(&s);
    /* Reset drops queued edges instead of replaying them later. */
    ready_line(&s, "R 215 2");
    for (int i = 0; i < 10; i++) { darwin_input_button(&s, i & 1); darwin_input_sync(&s); }
    assert(s.q_len > 0);
    darwin_input_reset(&s, "test");
    assert(s.q_len == 0 && s.inflight_n == 0 && s.cancel_pending && !s.contact_sent);
    /* Timeouts mark the guest lost; later input is dropped, not queued. */
    darwin_input_pump(&s);
    fake_clock += 11 * NANOSECONDS_PER_SECOND;
    check_timeouts(&s);
    assert(s.c_timeouts == 1 && s.guest_state == 'L');
    darwin_input_button(&s, true); darwin_input_sync(&s);
    assert(s.q_len == 0);
    /* Wheel notches batch into one W record, never while a finger is down. */
    ready_line(&s, "R 215 2");
    s.cancel_pending = false; s.inflight_n = 0; s.q_len = 0;
    darwin_input_button(&s, false); darwin_input_sync(&s);   /* host button still down above */
    darwin_input_wheel(&s, 1); darwin_input_wheel(&s, 1);
    assert(s.wheel_notches == 2 && s.q_len == 0);
    flush_wheel(&s);
    assert(s.q_len == 0);                       /* batch window still open */
    fake_clock += 50 * SCALE_MS;
    flush_wheel(&s);
    assert(count(" W 149 200 2 ") == 1 && s.wheel_notches == 0);
    for (int i = 0; i < 100; i++) {
        darwin_input_wheel(&s, -1);
        fake_clock += 50 * SCALE_MS;
        flush_wheel(&s);
    }
    assert(s.q_len == 1 && queue_tail(&s)->kind == 'W' && queue_tail(&s)->c == -8);
    assert(count(" W 149 200 ") == 1); /* slow guest never accumulates wheel drags */
    queue_clear(&s);
    darwin_input_button(&s, true); darwin_input_sync(&s);
    darwin_input_wheel(&s, -1);
    assert(s.wheel_notches == 0);
    darwin_input_button(&s, false); darwin_input_sync(&s);
    /* Console chatter around a reply is tolerated. */
    const char *noisy = "IOMFB: junk DVMI2A 9 9 S R 1\n";
    for (const char *p = noisy; *p; p++) tx_observer(&s, (uint8_t)*p);
    assert(s.c_ack_rejected >= 1 && s.guest_state == 'R');
    printf("ok sent=%" PRIu64 " acked=%" PRIu64 " coalesced=%" PRIu64 "\n", s.c_sent, s.c_acked, s.c_coalesced);
    return 0;
}
'''

    def test_queue_window_coalescing_and_recovery(self):
        harness = self.harness()
        # The timer section is not compiled; check_timeouts lives there, so
        # lift it into the harness explicitly.
        source = DEVICE.read_text()
        timeouts = source[source.index('static void check_timeouts('):source.index('static void write_status(')]
        harness = harness.replace('static DarwinInputState s;', timeouts + '\nstatic DarwinInputState s;')
        harness = harness.replace('static void darwin_input_pump(DarwinInputState *s);',
                                  'static void darwin_input_pump(DarwinInputState *s);\n'
                                  'static void check_timeouts(DarwinInputState *s);', 1)
        # Lifecycle helpers referenced by the harness body.
        lifecycle = source[source.index('void darwin_input_reset('):source.index('void darwin_input_init(')]
        harness = harness.replace('static DarwinInputState s;', lifecycle + '\nstatic DarwinInputState s;')
        with tempfile.TemporaryDirectory() as directory:
            binary = build(harness, directory, 'darwin-input-harness')
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn('ok sent=', result.stdout)

    def test_stale_replies_timeouts_and_partial_epoch(self):
        source = DEVICE.read_text()
        harness = self.harness().split('int main(void) {')[0]
        timeouts = source[source.index('static void check_timeouts('):source.index('static void write_status(')]
        harness += timeouts + r'''
int main(void) {
    s.enabled = true; s.epoch = 10; s.next_seq = 1;
    s.cancel_pending = true;
    darwin_input_pump(&s);
    assert(wire_len == 0); /* even cancel waits for a console reader */
    ready_line(&s, "I 87 0");
    ack_line(&s, "10 1 Q I 1");
    darwin_input_button(&s, true); darwin_input_sync(&s);
    assert(!s.contact_sent && s.q_len == 0);
    ready_line(&s, "R 87 10");
    darwin_input_button(&s, false); darwin_input_sync(&s);
    darwin_input_consumer(&s, 64, true);
    uint32_t seq = s.inflight[0].seq;
    char ack[80], done[80];
    snprintf(ack, sizeof(ack), "10 %u Q R 1", seq);
    snprintf(done, sizeof(done), "10 %u S 2", seq);
    ack_line(&s, ack);
    assert(s.inflight_n == 1); /* Q is acceptance, not dispatch */
    uint64_t acked = s.c_acked;
    ack_line(&s, ack);
    assert(s.c_acked == acked && s.inflight_n == 1);
    dispatch_line(&s, done);
    assert(s.inflight_n == 0 && s.c_dispatched == 1);
    dispatch_line(&s, done);
    assert(s.c_dispatched == 1);
    /* A fast worker can print its dispatch before the reader's ACK. */
    darwin_input_consumer(&s, 64, false);
    seq = s.inflight[0].seq;
    snprintf(ack, sizeof(ack), "10 %u Q R 1", seq);
    snprintf(done, sizeof(done), "10 %u S 2", seq);
    dispatch_line(&s, done);
    assert(s.inflight_n == 1);
    ack_line(&s, ack);
    assert(s.inflight_n == 0);
    for (int i = 0; i < 8; i++) darwin_input_consumer(&s, 64, i & 1);
    assert(s.q_len == 4 && s.inflight_n == 4);
    fake_clock += 11 * NANOSECONDS_PER_SECOND;
    check_timeouts(&s);
    assert(s.epoch == 11 && s.guest_state == 'L' && s.q_len == 0 && !s.inflight_n);
    ack_line(&s, ack);
    assert(s.guest_state == 'L'); /* stale R cannot revive input */
    /* Only cancel, not the queued button edges, follows the timeout. */
    size_t cut = wire_len;
    darwin_input_pump(&s);
    assert(wire_log[cut] == '\n' && !strstr(wire_log + cut, " B "));
    /* Simulate a prefix already accepted by the UART, with the FIFO full. */
    s.wire_len = 5; s.wire_pos = 2;
    memcpy(s.wire, "DVMI2", 5);
    memcpy(wire_log + wire_len, "DV", 2); wire_len += 2;
    new_epoch(&s, "partial UART record");
    cut = wire_len;
    darwin_input_pump(&s);
    assert(wire_log[cut] == '\n' && strstr(wire_log + cut, "DVMI2 12 "));
    /* A cancelled wheel batch must not retain the old expired deadline. */
    s.guest_state = 'R';
    darwin_input_wheel(&s, 1); darwin_input_wheel(&s, -1);
    assert(!s.wheel_notches && !s.wheel_deadline_vns);
    /* Once accepted, a slow HID call has its separate bounded deadline. */
    memset(&s, 0, sizeof(s));
    s.enabled = true; s.epoch = 20; s.next_seq = 1; s.guest_state = 'R';
    darwin_input_consumer(&s, 64, true);
    ack_line(&s, "20 1 Q R 1");
    fake_clock += 11 * NANOSECONDS_PER_SECOND;
    check_timeouts(&s);
    assert(!s.c_timeouts && s.inflight_n == 1);
    fake_clock += 20 * NANOSECONDS_PER_SECOND;
    check_timeouts(&s);
    assert(s.c_timeouts == 1 && s.epoch == 21 && s.guest_state == 'L');
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = build(harness, directory, 'input-failure-paths')
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
