"""Read selected DRAM pages from one paused VM through its explicit HMP.

Use when task pmap and retained object addresses are already known. This is
not a process/crash scan and cannot prove absence elsewhere in memory. Pages
are saved under a new artifact directory, so later reads in this session use
the same bytes. Never resume the VM while using this reader.
"""
from pathlib import Path
from warm_boot_postmortem import Memory


class SparseMemory(Memory):
    def __init__(self, monitor, directory, ram_size=0x300000000):
        directory = Path(directory).resolve()
        if any(c in str(directory) for c in ('"', '\n', '\r', '\\')):
            raise ValueError('capture path cannot be safely quoted for HMP')
        directory.mkdir(exist_ok=False)
        super().__init__(Path(monitor), directory)
        self.directory = directory
        self.ram_end = 0x10000000000 + ram_size
        self.captured = {}

    def physical(self, address, size):
        if not 0x10000000000 <= address <= address + size <= self.ram_end:
            raise ValueError('sparse reader only reads guest DRAM')
        result = bytearray()
        while len(result) < size:
            current = address + len(result)
            page = current & ~0x3fff
            if page not in self.captured:
                if 'paused' not in self.monitor.command('info status'):
                    raise RuntimeError('source VM resumed during sparse capture')
                path = self.directory / f'{page:011x}.bin'
                self.monitor.command(f'pmemsave {page:#x} 0x4000 "{path}"')
                raw = path.read_bytes()
                if len(raw) != 0x4000:
                    raise RuntimeError('incomplete DRAM page')
                self.captured[page] = raw
            count = min(size - len(result), 0x4000 - (current & 0x3fff))
            result += self.captured[page][current & 0x3fff:(current & 0x3fff)+count]
        return bytes(result)
