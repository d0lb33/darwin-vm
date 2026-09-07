"""Verify drained raw audit slots and the suffix still retained in final RAM."""
import base64
import struct
import zlib


def verify_audits(audits, raw):
    if len(raw)!=16*1024*1024 or not audits or len(audits)>8192:
        raise ValueError('job audit extent')
    head,=struct.unpack_from('<Q',raw,0x180)
    captured=all('slot_v1' in a for a in audits)
    if not captured and (len(audits)>120 or any('slot_v1' in a for a in audits)):
        raise ValueError('mixed or missing drained audit capture')
    previous_head=0
    for i,a in enumerate(audits):
        seq=a['seq'];offset=0x1000+((seq-1)%120)*512
        if seq<=0 or seq>head or (i and seq!=audits[i-1]['seq']+1):
            raise ValueError('audit sequence/retention')
        if captured:
            c=a['slot_v1'];observed=c['head']
            if c['session']!=raw[16:32].hex() or not previous_head<=observed<=head or not 0<=observed-seq<120:
                raise ValueError('drained audit session/head')
            previous_head=observed
            slot=base64.b64decode(c['bytes'],validate=True)
        else:
            if head-seq>=120:raise ValueError('audit sequence/retention')
            slot=raw[offset:offset+512]
        if len(slot)<16:raise ValueError('audit slot extent')
        actual,n,crc=struct.unpack_from('<QII',slot)
        if actual!=seq or not 0<n<480 or len(slot)<16+n or (captured and len(slot)!=16+n):
            raise ValueError('audit slot framing')
        data=slot[16:16+n]
        if zlib.crc32(data)!=crc or data.decode().strip()!=a['line']:
            raise ValueError('audit content/CRC')
        if captured and head-seq<120 and raw[offset:offset+16+n]!=slot:
            raise ValueError('drained audit disagrees with retained final slot')
    return 'drained-slots-v1' if captured else 'final-ring'
