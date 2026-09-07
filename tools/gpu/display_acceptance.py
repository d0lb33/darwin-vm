"""Pair native presentations with subsequent D594 replies, including reused IDs."""
import re


class DisplayAcceptance:
    def __init__(self):
        self.presentations = 0
        self.completions = 0
        self.pending = None
        self.failure = None

    def feed(self, line):
        presented = re.search(r'^iomfb: presented .* swap=(\d+) ', line)
        if presented:
            if self.pending is not None:
                self.failure = 'presentation before previous native completion'
            self.pending = int(presented[1])
            self.presentations += 1
        done = re.search(r'^iomfb: swap id (\d+) D594 (?:nested )?completed, status 0x([0-9a-f]+)', line)
        if done:
            if int(done[2], 16):
                self.failure = 'native D594 completion returned failure: ' + line
            elif self.pending is not None:
                if int(done[1]) != self.pending:
                    self.failure = 'native D594 completion identity mismatch'
                else:
                    self.pending = None
                    self.completions += 1

    def reached(self, count):
        return not self.failure and self.pending is None and self.completions >= count
