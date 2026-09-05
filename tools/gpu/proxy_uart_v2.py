"""Reliable diagnostic transport; session-local retries never replay byte delivery."""
import os
import select
import time
from proxy_uart import ProxyUART
from uart_link import Parser,encode,CHUNK

class ReliableProxyUART(ProxyUART):
    def __init__(self,executable,output,library_cache=None,faults=False):
        super().__init__(executable,output,library_cache)
        self.parser=Parser();self.session=None;self.wire_out=bytearray()
        self.last_guest=None;self.tx=None;self.tx_next=0;self.attempts=0
        self.retries=0;self.duplicates=0;self.close_status=None
        self.faults=faults;self.injected=set();self.close_record=None
    def line(self,line):
        pass  # Unprotected console markers cannot establish success.
    def feed(self,data):
        for session,kind,offset,payload in self.parser.feed(data):
            if kind=='R' and not offset and not payload:
                if self.session is None:
                    self.session=session;self.ready=True;self.event(event='ready',session=session)
                if session==self.session:self.wire_out+=encode(session,'r')
                continue
            if session!=self.session:continue
            if kind=='G' and payload:
                frame=(offset,payload)
                if offset==self.guest_offset and not self.finished:
                    if len(self.to_worker)+len(payload)>1024*1024:raise RuntimeError('worker queue limit')
                    self.to_worker+=payload;self.requests.write(payload);self.requests.flush()
                    self.guest_offset+=len(payload);self.last_guest=frame
                elif frame==self.last_guest:
                    self.duplicates+=1
                else:raise RuntimeError(f'guest stream offset/content mismatch at {offset}')
                if self.faults and 'drop-g-ack' not in self.injected:
                    self.injected.add('drop-g-ack');self.event(event='injected',fault='drop-g-ack')
                else:self.wire_out+=encode(session,'g',self.guest_offset)
            elif kind=='h' and not payload:
                if self.faults and 'drop-h-ack' not in self.injected:
                    self.injected.add('drop-h-ack');self.event(event='injected',fault='drop-h-ack');continue
                if self.tx and offset==self.response_offset+len(self.tx):
                    self.response_offset=offset;self.tx=None;self.attempts=0
                elif offset>self.response_offset:raise RuntimeError('unexpected host-data ACK')
            elif kind=='C' and len(payload)==12:
                received=int.from_bytes(payload[:8],'big');status=int.from_bytes(payload[8:],'big')
                record=(offset,received,status)
                if self.finished:
                    if record!=self.close_record:raise RuntimeError('conflicting close record')
                else:
                    if self.tx and received==self.response_offset+len(self.tx):
                        self.response_offset=received;self.tx=None
                    if offset!=self.guest_offset or received!=self.response_offset or self.to_guest:
                        raise RuntimeError('close has unacknowledged or missing stream bytes')
                    self.finished=True;self.close_status=status;self.close_record=record
                    self.event(event='closed',request_bytes=offset,response_bytes=received,wait_status=status)
                self.wire_out+=encode(session,'c',offset)
    def pump(self,uart):
        if self.proc.poll() is not None:raise RuntimeError(f'host worker exited {self.proc.returncode}')
        readable,writable,_=select.select([self.proc.stdout],[self.proc.stdin] if self.to_worker else [],[],0)
        if writable:
            n=os.write(self.proc.stdin.fileno(),self.to_worker[:65536]);del self.to_worker[:n]
        if readable:
            data=os.read(self.proc.stdout.fileno(),65536)
            if not data:raise RuntimeError('host response pipe closed')
            self.responses.write(data);self.responses.flush();self.to_guest+=data
        if len(self.to_guest)>1024*1024:raise RuntimeError('response queue limit')
        if self.ready and not self.finished:
            if self.tx is None and self.to_guest:
                self.tx=bytes(self.to_guest[:CHUNK]);del self.to_guest[:len(self.tx)]
                self.tx_next=0;self.attempts=0
            if self.tx is not None and time.monotonic()>=self.tx_next:
                if self.attempts>=80:raise RuntimeError('guest response ACK deadline')
                if self.attempts:self.retries+=1
                self.attempts+=1;self.tx_next=time.monotonic()+.5
                frame=encode(self.session,'H',self.response_offset,self.tx)
                if self.faults and 'corrupt-h' not in self.injected:
                    self.injected.add('corrupt-h');self.event(event='injected',fault='corrupt-h')
                    frame=frame[:42]+b'kernel noise\r\n'+frame[42:]
                self.wire_out+=frame
        if self.wire_out:
            try:
                n=uart.send(self.wire_out);del self.wire_out[:n]
            except BlockingIOError:pass
        if len(self.wire_out)>65536:raise RuntimeError('UART output queue limit')
    def close(self):
        self.event(event='link-stats',retries=self.retries,duplicates=self.duplicates,
            rejected=self.parser.rejected,injected=sorted(self.injected),wait_status=self.close_status)
        super().close()
