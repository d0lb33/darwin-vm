"""One-shot diagnostic: host Metal bytes -> native guest IOSurface backing.
Run only on a disposable VM at iomfb_scanout; uses QEMU GPA writes/dirty marking.
Not a guest transport, IOSurface allocation implementation, or fence protocol.
"""
import hashlib, json, struct, re
from pathlib import Path
import lldb
STATE = {}
def expr(frame, code):
    opts=lldb.SBExpressionOptions();opts.SetLanguage(lldb.eLanguageTypeC_plus_plus_17);opts.SetIgnoreBreakpoints(True);opts.SetTimeoutInMicroSeconds(5000000)
    value=frame.EvaluateExpression(code,opts)
    if value.GetError().Fail(): raise RuntimeError(value.GetError().GetCString())
    return value.GetValueAsUnsigned()
def patch(frame, location, _):
    location.GetBreakpoint().SetEnabled(False)
    process=frame.GetThread().GetProcess(); rec={'diagnostic_only':True}
    try:
        assert expr(frame,'((bool (*)())0x%x)()' % STATE['get_bql_locked']) == 1
        pointer=frame.FindRegister('x19').GetValueAsUnsigned();length=frame.FindRegister('x20').GetValueAsUnsigned()
        assert pointer and 0xff0<=length<=0x2000,(pointer,length)
        e=lldb.SBError();body=process.ReadMemory(pointer,length,e);assert e.Success()
        desc=0x6e0
        fmt=struct.unpack_from('<I',body,desc+0x0b)[0]
        stride=struct.unpack_from('<I',body,desc+0x15)[0]
        width,height,size=struct.unpack_from('<III',body,desc+0x21)
        dva=struct.unpack_from('<Q',body,0xf90)[0]
        assert fmt==0x42475241 and width==1179 and height==2556 and stride==4864,(fmt,width,height,stride)
        assert body[0xfea:0xfef]==bytes([0,0,1,1,1]),body[0xfea:0xfef].hex()
        pixels=Path(STATE['pixels']).read_bytes();assert len(pixels)==64*48*4
        host=process.AllocateMemory(len(pixels)*3,lldb.ePermissionsReadable|lldb.ePermissionsWritable,e);assert e.Success()
        assert process.WriteMemory(host,pixels,e)==len(pixels) and e.Success()
        # Snapshot original guest bytes; write only the 64x48 rectangle at (100,100).
        dart=frame.FindRegister('x0').GetValueAsUnsigned(); assert dart
        code='''({ struct Attr { unsigned int bits; bool unspecified; unsigned char r1; unsigned short r2; };
          void *d=(void *)DART; void *as=(void *)ASPACE;
          auto translate=(bool (*)(void *,unsigned int,unsigned long long,unsigned long long *))TRANSLATE;
          auto read=(unsigned int (*)(void *,unsigned long long,Attr,void *,unsigned long long))READ;
          auto write=(unsigned int (*)(void *,unsigned long long,Attr,const void *,unsigned long long))WRITE;
          unsigned char *src=(unsigned char *)0x%x, *old=src+12288, *got=old+12288;
          unsigned long long base=0x%x; int rc=0;
          for(unsigned y=0;y<48 && !rc;y++) for(unsigned x=0;x<256 && !rc;) {
            unsigned long long va=base+(y+100)*4864+400+x, pa=0;
            unsigned n=256-x; if(n>4096-(va&4095)) n=4096-(va&4095);
            if(!translate(d,0,va,&pa)) {rc=1;break;}
            if(read(as,pa,Attr{0,true,0,0},old+y*256+x,n)) {rc=2;break;}
            if(write(as,pa,Attr{0,true,0,0},src+y*256+x,n)) {rc=3;break;}
            if(read(as,pa,Attr{0,true,0,0},got+y*256+x,n)) {rc=4;break;}
            x+=n;
          } rc; })''' % (host,dva)
        for token,value in [('DART',dart),('ASPACE',STATE['address_space_memory']),('TRANSLATE',STATE['darwin_dart_translate']),('READ',STATE['address_space_read_full']),('WRITE',STATE['address_space_write'])]: code=code.replace(token,'0x%x'%value)
        for word in ['d','as','src','old','got','base','rc','y','x','va','pa','n','translate','read','write','Attr']:
            code=re.sub(r'\b'+word+r'\b','dvm_'+word,code)
        rc=expr(frame,code);assert rc==0,rc
        old=process.ReadMemory(host+12288,12288,e);assert e.Success()
        got=process.ReadMemory(host+24576,12288,e);assert e.Success() and got==pixels
        out=Path(STATE['out']);(out/'original-rectangle.bgra').write_bytes(old);(out/'guest-readback.bgra').write_bytes(got)
        rec.update(ok=True,dva=hex(dva),width=width,height=height,stride=stride,size=size,rectangle=[100,100,64,48],host_bytes_sha256=hashlib.sha256(pixels).hexdigest(),readback_sha256=hashlib.sha256(got).hexdigest(),original_sha256=hashlib.sha256(old).hexdigest(),request_sha256=hashlib.sha256(body).hexdigest())
        # Stop here; driver steps out of iomfb_scanout through the existing copy.
    except Exception as ex:
        rec.update(ok=False,error=str(ex))
    Path(STATE['out'],'patch-result.json').write_text(json.dumps(rec,indent=2)+'\n')
    print('GPU_SURFACE_PATCH '+json.dumps(rec),flush=True)
    return True
def install(debugger,pixels,out,pid):
    target=debugger.GetSelectedTarget();assert target.GetProcess().GetProcessID()==pid
    assert Path(out,'disk.qcow2').is_file() and Path(out,'qemu.pid').read_text().strip()==str(pid)
    binary=Path(str(target.GetExecutable().fullpath))
    assert hashlib.sha256(binary.read_bytes()).hexdigest()=='9d39357bd771a1089725654afc077de8925cdceca456b3561a0fd2def760abbd', 'probe offsets require pinned QEMU'
    STATE.update(pixels=pixels,out=out)
    for name in ['get_bql_locked','address_space_memory','darwin_dart_translate','address_space_read_full','address_space_write','vm_stop','iomfb_scanout']:
        symbols=target.FindSymbols(name); assert symbols.GetSize()==1,(name,symbols.GetSize())
        STATE[name]=symbols.GetContextAtIndex(0).GetSymbol().GetStartAddress().GetLoadAddress(target)
    # Pinned QEMU disassembly: +80 follows g_hash_table_lookup; x0=dart, x19=input, x20=len.
    bp=target.BreakpointCreateByAddress(STATE['iomfb_scanout']+80);assert bp.GetNumLocations()==1
    bp.SetScriptCallbackFunction(__name__+'.patch')
    print('GPU_SURFACE_READY',flush=True)

def pause(debugger):
    f=debugger.GetSelectedTarget().GetProcess().GetSelectedThread().GetFrameAtIndex(0)
    # Exact pinned QAPI enum: RUN_STATE_PAUSED=4.
    print('VM_STOP_RESULT',expr(f,'((int (*)(int))0x%x)(4)' % STATE['vm_stop']),flush=True)
