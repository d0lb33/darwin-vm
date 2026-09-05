"""Wire-compatible codec for uart_link.h. CRC is integrity, not authentication."""
import re
import zlib
CHUNK=128
MAX=384
PATTERN=re.compile(rb'D2:([0-9a-f]{8}):([RrGgHhCc]):([0-9a-f]{16}):([0-9a-f]{4}):([0-9a-f]*):([0-9a-f]{8})')

def encode(session,kind,offset=0,data=b''):
    if not (0<=session<2**32 and 0<=offset<2**64 and len(data)<=CHUNK and len(kind)==1 and kind in 'RrGgHhCc'):
        raise ValueError('invalid UART frame fields')
    body=f'D2:{session:08x}:{kind}:{offset:016x}:{len(data):04x}:{data.hex()}'.encode()
    return b'~'+body+f':{zlib.crc32(body):08x}~\n'.encode()

class Parser:
    def __init__(self):
        self.buffer=bytearray();self.overflow=False;self.rejected=0
    def feed(self,data):
        frames=[]
        for byte in data:
            if byte==ord('~'):
                m=None if self.overflow else PATTERN.fullmatch(self.buffer)
                if m and len(m[5])==int(m[4],16)*2 and int(m[4],16)<=CHUNK and zlib.crc32(self.buffer[:-9])==int(m[6],16):
                    frames.append((int(m[1],16),m[2].decode(),int(m[3],16),bytes.fromhex(m[5].decode())))
                elif self.buffer.startswith(b'D2:'):self.rejected+=1
                self.buffer.clear();self.overflow=False
            elif len(self.buffer)<MAX and not self.overflow:self.buffer.append(byte)
            else:self.overflow=True
        return frames
