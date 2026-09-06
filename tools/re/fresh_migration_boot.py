#!/usr/bin/env python3
"""Launch a paused fresh-disk migration VM from a sealed bootstrap seed.

Uses a recorded launch for firmware/device configuration, removes saved RAM,
plugins and debugger, and creates an exclusive child disk. No timing until the
caller resumes. Explicit --diagnostic-plugin opts into instrumentation and
records that the run is not a clean control. Owns only its newly created tag
directory and QEMU process.
"""
import argparse,json,os,re,struct,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from checkpoint_common import SAFE_TAG,atomic_json,sha256,wait_for_path
from warm_boot_probe import boot_command
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--launch',type=Path,required=True)
p.add_argument('--seed',type=Path,required=True)
p.add_argument('--qemu',type=Path,required=True)
p.add_argument('--tag',required=True)
p.add_argument('--diagnostic-plugin', action='append', default=[],
               help='Explicit diagnostic-only QEMU plugin; disqualifies this run as a clean timing control')
p.add_argument('--framebuffer', help='Diagnostic pixel geometry, e.g. 786x1704; updates framebuffer and measured DCP size replies together')
a=p.parse_args()
if not SAFE_TAG.fullmatch(a.tag) or len(a.tag)>40:p.error('invalid tag')
seed=a.seed.resolve(strict=True);binary=a.qemu.resolve(strict=True)
if seed.stat().st_mode&0o222:p.error('seed must be sealed read-only')
out=Path('/tmp/dvm')/a.tag;out.mkdir(exist_ok=False)
source=json.loads(a.launch.read_text());args=boot_command(source['argv'],out)
clean=[];i=0
while i<len(args):
 if args[i] in ('-plugin','-display'):i+=2
 else:clean.append(args[i]);i+=1
clean[0]=str(binary);clean+=['-display','none','-S']
for plugin in a.diagnostic_plugin:
 clean+=['-plugin',plugin]
subprocess.run(['qemu-img','create','-f','qcow2','-F','qcow2','-b',str(seed),str(out/'disk.qcow2')],check=True)
model={k:v for k,v in source['env'].items() if k.startswith('DARWIN_')}
if a.framebuffer:
 match=re.fullmatch(r'([1-9][0-9]*)x([1-9][0-9]*)',a.framebuffer)
 if not match:p.error('framebuffer must be WIDTHxHEIGHT')
 width,height=map(int,match.groups())
 if not (320<=width<=4096 and 320<=height<=4096):p.error('diagnostic framebuffer dimensions must be 320..4096')
 if clean.count('-fb')!=1:p.error('source must contain exactly one framebuffer argument')
 # Keep the host scanout allocation and the two observed native DCP geometry
 # responses coherent. Do not change UIKit scale or override snapshot format.
 payload=struct.pack('<II',width,height).hex()
 cb,count=re.subn(r'D586:[0-9a-fA-F]{16}:',f'D586:{payload}:',model.get('DARWIN_DCP_IOMFB_CB',''))
 out_reply,out_count=re.subn(r'A453=[0-9a-fA-F]{16}(?=,|$)',f'A453={payload}',model.get('DARWIN_DCP_IOMFB_OUT',''))
 if count!=1 or out_count!=1:p.error('source must have one measured D586 and A453 size payload')
 clean[clean.index('-fb')+1]=a.framebuffer
 model['DARWIN_DCP_IOMFB_CB']=cb
 model['DARWIN_DCP_IOMFB_OUT']=out_reply
model['DARWIN_SCRD_CONTEXTS']='1'
model['DARWIN_INPUT_STATUS']=str(out/'input-status.json')
model['DARWIN_TOUCH_EVENTS']=str(out/'events.jsonl')
env={k:v for k,v in os.environ.items() if not k.startswith(('DARWIN_','GXFSTAT_','DVM_'))};env.update(model)
atomic_json(out/'launch.json',dict(format='darwin-vm-qemu-launch-v1',argv=clean,env=model))
atomic_json(out/'fresh-source.json',dict(seed=str(seed),seed_sha256=sha256(seed),qemu_sha256=sha256(binary),ram_restored=False,plugins=bool(a.diagnostic_plugin),diagnostic_plugins=a.diagnostic_plugin,debugger=False))
with (out/'qemu.stderr.log').open('wb') as err,(out/'host.stdout.log').open('wb') as stdout:
 proc=subprocess.Popen(clean,env=env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=err,start_new_session=True)
(out/'qemu.pid').write_text(str(proc.pid)+'\n')
wait_for_path(out/'monitor.sock',time.monotonic()+30)
if proc.poll() is not None:raise RuntimeError('QEMU exited during launch')
print(json.dumps(dict(pid=proc.pid,run=str(out),monitor=str(out/'monitor.sock'),paused=True)))
