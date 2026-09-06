"""Independent Python parser/encoder for the bounded DVB1 comparison and evidence.

Only the worker executes shaders. This parser records raw-byte SHA/contents and
normalizes results for the existing nonce oracle verifier.
"""
import base64
import struct

REQUEST=0x31425644
REPLY=0x31525644
SIZE=28672

def payload(value):
    return base64.b64decode(value,validate=True) if isinstance(value,str) else value

def encode_request(r):
    out=bytearray(SIZE)
    struct.pack_into('<IIQIIII',out,0,REQUEST,1,r['seq'],SIZE,2,3,2)
    if len(r['commands'])!=2 or len(r['uploads'])!=3 or len(r['readbacks'])!=2:raise ValueError('binary counts')
    for i,c in enumerate(r['commands']):
        bs={b['index']:b for b in c['buffers']};b1=bs.get(1,{});b2=bs[2]
        struct.pack_into('<6Q7I20s',out,32+128*i,c['pipeline'],c['textures'][0] if c['textures'] else 0,
            b1.get('buffer',0),b2['buffer'],b1.get('offset',0),b2['offset'],
            *c['groups'],*c['threads'],c['threadgroupMemory'][0]['length'],payload(c['bytes'][0]['data']))
    seen=set()
    for i,u in enumerate(r['uploads']):
        data=payload(u['data']);buffer='buffer' in u
        off={(True,96):512,(True,16):640,(False,24576):4096}.get((buffer,len(data)))
        if off is None or off in seen:raise ValueError('binary staging slots')
        seen.add(off)
        struct.pack_into('<QIIIIQ',out,288+32*i,u['buffer' if buffer else 'texture'],1 if buffer else 2,0 if buffer else u['row'],off,len(data),0)
        out[off:off+len(data)]=data
    struct.pack_into('<2Q',out,384,*r['readbacks'])
    return bytes(out)

def decode_request(raw):
    if len(raw)!=SIZE:raise ValueError('binary size')
    magic,version,seq,n,nc,nu,nr=struct.unpack_from('<IIQIIII',raw)
    if (magic,version,n,nc,nu,nr)!=(REQUEST,1,SIZE,2,3,2) or not seq or any(raw[400:512]):raise ValueError('binary header')
    commands=[];uploads=[];seen=set()
    for i in range(2):
        d=struct.unpack_from('<6Q7I20s',raw,32+128*i)
        if any(raw[32+128*i+96:32+128*i+128]):raise ValueError('command padding')
        p,t,b1,b2,o1,o2=d[:6]
        bs=([dict(index=1,buffer=b1,offset=o1)] if b1 else [])+[dict(index=2,buffer=b2,offset=o2)]
        commands.append(dict(pipeline=p,textures=[t] if t else [],buffers=bs,groups=list(d[6:9]),threads=list(d[9:12]),
            threadgroupMemory=[dict(index=0,length=d[12])],bytes=[dict(index=0,data=base64.b64encode(d[13]).decode())]))
    for i in range(3):
        handle,kind,row,off,n,reserved=struct.unpack_from('<QIIIIQ',raw,288+32*i)
        if (kind,n,off,row) not in ((1,96,512,0),(1,16,640,0),(2,24576,4096,512)) or off in seen or reserved or not handle:raise ValueError('binary slot bounds')
        seen.add(off)
        uploads.append({('buffer' if kind==1 else 'texture'):handle,'row':row,'data':base64.b64encode(raw[off:off+n]).decode()})
    return dict(seq=seq,op='submit',commands=commands,uploads=uploads,readbacks=list(struct.unpack_from('<2Q',raw,384)))

def decode_reply(raw):
    if len(raw)!=272:raise ValueError('binary reply length')
    magic,version,seq,n,status,dispatches,reads,gpu,h0,h1=struct.unpack_from('<IIQIIII3Q',raw)
    if (magic,version,n,status,dispatches,reads)!=(REPLY,1,272,4,2,2) or not seq or not h0 or not h1 or h0==h1 or any(raw[56:128]) or any(raw[224:256]):raise ValueError('binary reply contract')
    return dict(seq=seq,ok=True,status=status,dispatches=dispatches,gpu_us=gpu,buffers={str(h0):base64.b64encode(raw[128:224]).decode(),str(h1):base64.b64encode(raw[256:272]).decode()})
