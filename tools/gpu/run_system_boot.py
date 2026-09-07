#!/usr/bin/env python3
"""Bounded boot of a backboardd-only Metal registration image; no test consumer."""
import argparse,json,os,select,socket,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP,SAFE_TAG,sha256,verify_backing_chain
from driver_mmio_peer import MMIOPeer

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest',type=Path);p.add_argument('--worker',type=Path,required=True)
    p.add_argument('--library',type=Path,required=True);p.add_argument('--tag',required=True)
    p.add_argument('--library-cache',type=Path,help='explicit content-addressed exact-guest AIR directory')
    p.add_argument('--seconds',type=int,default=300)
    p.add_argument('--runtime-probe',action='store_true',help='reuse runner inbox/package transport for the bounded backboardd loading probe')
    a=p.parse_args()
    if not SAFE_TAG.fullmatch(a.tag) or len(a.tag)>40 or not 30<=a.seconds<=600:p.error('invalid tag/deadline')
    m=json.loads(a.manifest.read_text());verify_backing_chain(m['disk']['backing_chain'])
    for name,item in m['qemu_inputs'].items():
        if sha256(Path(name))!=item['sha256']:raise ValueError('changed pinned input '+name)
    if not m.get('guest_installation',{}).get('scope','').startswith('backboardd-only boot registration'):raise ValueError('requires owned compositor installation')
    out=Path('/tmp/dvm')/a.tag;out.mkdir(exist_ok=False)
    if a.runtime_probe:
        from driver_runner_peer import RunnerPeer
        peer=RunnerPeer(out,a.worker,a.library,boot=True,library_cache=a.library_cache)
    else:peer=MMIOPeer(out,a.worker,a.library,boot=True,library_cache=a.library_cache)
    peer.runner=True
    if not peer.managed:raise ValueError('requires current managed mode-3 worker')
    subprocess.run(['qemu-img','create','-f','qcow2','-F','qcow2','-b',m['disk']['path'],str(out/'disk.qcow2')],check=True)
    argv=list(m['qemu_argv']);argv[argv.index('-drive')+1]=f'if=none,id=ans,file={out}/disk.qcow2,format=qcow2'
    if any(x in argv for x in ('-monitor','-qmp','-serial','-chardev','-gdb','-s','-S','-incoming')):raise ValueError('unexpected source control endpoint')
    argv+=['-monitor',f'unix:{out}/monitor.sock,server=on,wait=off','-qmp',f'unix:{out}/qmp.sock,server=on,wait=off',
        '-chardev',f'socket,id=gpu_uart,path={out}/uart.sock,server=on,wait=off,logfile={out}/serial.log','-serial','chardev:gpu_uart',
        '-chardev',f'socket,id=dvm_gpu_notify,path={out}/gpu-notify.sock,server=on,wait=off']
    model=dict(m['qemu_env'])
    if any(k.startswith('DARWIN_GPU_') for k in model):raise ValueError('uncontrolled source GPU transport')
    model.update(DARWIN_GPU_SHM_PATH=str(out/'shared-ram.bin'),DARWIN_GPU_MANAGED_RAM_PATH=str(out/'managed-ram.bin'),
        DARWIN_GPU_MANAGED_PAGES_PATH=str(out/'managed-pages.bin'),DARWIN_GPU_PRESENT_TRANSPORT='1',DARWIN_DCP_GPU_PRESENT_DIR=str(out),
        DARWIN_INPUT_STATUS=str(out/'input-status.json'),DARWIN_TOUCH_EVENTS=str(out/'events.jsonl'))
    env={k:v for k,v in os.environ.items() if not k.startswith(('DVM_','DARWIN_','GXFSTAT_'))};env.update(model)
    (out/'launch.json').write_text(json.dumps(dict(argv=argv,env=model),indent=2)+'\n')
    (out/'source-manifest.json').write_bytes(a.manifest.read_bytes())
    report=dict(scope='actual backboardd boot integration; no injected scene or test-helper factory',deadline=a.seconds,
        registered=False,debugger=False,ram_restored=False,final_pixels_verified=False)
    proc=None;uart=None;start=time.monotonic();failure_at=None;serial_offset=0;stderr_offset=0;tail='';errtail=''
    try:
        with (out/'stderr.log').open('wb') as log:
            proc=subprocess.Popen(argv,env=env,stdout=log,stderr=subprocess.STDOUT)
            (out/'qemu.pid').write_text(str(proc.pid)+'\n')
            while proc.poll() is None and time.monotonic()-start<a.seconds:
                if uart is None and (out/'uart.sock').exists():
                    uart=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);uart.connect(str(out/'uart.sock'));uart.setblocking(False)
                peer.pump();audit=peer.audit()
                for line in audit:print(line,flush=True)
                if (out/'serial.log').exists():
                    with (out/'serial.log').open('rb') as f:f.seek(serial_offset);chunk=f.read();serial_offset=f.tell()
                    tail=(tail+chunk.decode(errors='replace'))[-131072:]
                    for line in chunk.decode(errors='replace').splitlines():
                        if 'GPU_LOAD_SYSTEM_' in line:print(line,flush=True)
                with (out/'stderr.log').open('rb') as f:f.seek(stderr_offset);chunk=f.read();stderr_offset=f.tell()
                errtail=(errtail+chunk.decode(errors='replace'))[-131072:]
                combined=tail+'\n'.join(audit)
                if 'GPU_LOAD_SYSTEM_REGISTERED' in combined:report['registered']=True
                if failure_at is None and any(s in combined for s in ('GPU_LOAD_SYSTEM_MISSING','GPU_LOAD_SYSTEM_UNCAUGHT','GPU_LOAD_SYSTEM_EXCEPTION','GPU_LOAD_TEXTURE_REJECT','panic(cpu','GPU_LOAD_ERROR')):
                    failure_at=time.monotonic();report['stop_reason']='first failed boot/compositor contract';report['failure_context']=combined[-32768:]
                if failure_at is None and peer.records and not peer.records[-1]['reply'].get('ok',False):
                    failure_at=time.monotonic();report['stop_reason']='first rejected compositor host request';report['failed_request']=peer.records[-1]
                # Do not serve a replacement compositor after the owner has
                # failed. Retained host resources have no restart contract.
                if failure_at:break
                # Native presentation and actual backend render replies are
                # recorded separately. Neither alone verifies final pixels.
                report['render_submissions']=sum(r['reply'].get('ok',False) and bool(r['reply'].get('renderPasses',r['reply'].get('passes') if r['op']=='renderSubmit' else 0)) for r in peer.records)
                report['blit_submissions']=sum(r['reply'].get('ok',False) and bool(r['reply'].get('blitPasses')) for r in peer.records)
                if report['registered'] and report['render_submissions'] and 'iomfb: presented ' in errtail:
                    report['stop_reason']='registered compositor submitted render work and native display presented';break
                readers=([uart] if uart else [])+([peer.sock] if peer.sock else [])
                if readers:
                    ready,_,_=select.select(readers,[],[],.02)
                    if uart in ready:
                        try:uart.recv(65536)
                        except BlockingIOError:pass
                else:time.sleep(.02)
            report.setdefault('stop_reason','boot/readiness deadline' if proc.poll() is None else 'QEMU exited')
            if proc.poll() is None:
                monitor=HMP(out/'monitor.sock',timeout=5)
                monitor.command('stop')
                report['screendump_result']=monitor.command(f'screendump "{out}/scanout.ppm"')
                monitor.command('quit');proc.wait(timeout=15)
    except Exception as e:
        report['orchestration_error']=repr(e);raise
    finally:
        if uart:uart.close()
        if proc and proc.poll() is None:proc.terminate();proc.wait(timeout=15)
        report['elapsed']=time.monotonic()-start
        report['host_requests']=len(peer.records)
        report['host_errors']=[r for r in peer.records if not r['reply'].get('ok')]
        (out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        peer.close()
        verify_backing_chain(m['disk']['backing_chain'])
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
