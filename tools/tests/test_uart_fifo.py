"""Compile the device's FIFO routines; regress full-buffer packet loss."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class UARTFIFOTests(unittest.TestCase):
    def test_full_wrap_and_backpressure_preserve_packet_bytes(self):
        source = (ROOT/'qemu-sptm/hw/char/exynos4210_uart.c').read_text()
        start = source.rindex('typedef struct {', 0, source.index('} Exynos4210UartFIFO;'))
        structure = source[start:source.index('} Exynos4210UartFIFO;')+len('} Exynos4210UartFIFO;')]
        routines = source[source.index('static void fifo_store('):
                          source.index('static uint32_t exynos4210_uart_FIFO_trigger_level(')]
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#define g_free free
#define g_malloc0(n) calloc(1, (n))
'''+structure+routines+r'''
int main(void) {
    for (unsigned size = 1; size <= 256; size *= 2) {
        Exynos4210UartFIFO q = {.size = size};
        fifo_reset(&q);
        unsigned sent = 0, received = 0;
        for (unsigned round = 0; round < 100; round++) {
            while (fifo_empty_elements_number(&q)) {
                fifo_store(&q, (uint8_t)sent++);
            }
            assert((unsigned)fifo_elements_number(&q) == size);
            assert(q.sp == q.rp);
            /* A break/full store cannot overwrite unread bytes. */
            fifo_store(&q, 0xff);
            unsigned drain = 1 + round % size;
            while (drain--) {
                assert(fifo_retrieve(&q) == (uint8_t)received++);
            }
            assert((unsigned)fifo_elements_number(&q) == sent-received);
        }
        while (fifo_elements_number(&q)) {
            assert(fifo_retrieve(&q) == (uint8_t)received++);
        }
        assert(sent == received);
        fifo_store(&q, 42);
        fifo_reset(&q);
        assert(!fifo_elements_number(&q));
        assert((unsigned)fifo_empty_elements_number(&q) == size);
        free(q.data);
    }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            c = Path(directory)/'fifo.c'
            binary = Path(directory)/'fifo'
            c.write_text(harness)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
