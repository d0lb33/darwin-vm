#!/usr/bin/env python3
"""Run a bounded, sequential shared-surface matrix, recover display, and stop.

Plan rows describe signed bundles/workers and expected frame/rate contracts.
The final row must be the installed control. No shell commands come from plans.
"""
import argparse
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from checkpoint_common import SAFE_TAG,atomic_json
from verify_runner_job import verify
from shared_consumer_verify import verify_scanout_export


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--tag',required=True);p.add_argument('--worker',type=Path,required=True)
    p.add_argument('--library',type=Path,required=True)
    a=p.parse_args()
    if not SAFE_TAG.fullmatch(a.tag):p.error('invalid tag')
    plan=json.loads(a.plan.read_text())
    if not 2<=len(plan)<=8 or plan[-1].get('mode')!='installed' or any(x.get('mode','data')!='data' for x in plan[:-1]):
        p.error('requires shared runtime jobs followed by one installed control')
    src=Path(__file__).resolve().parent;repo=src.parent.parent;trial=Path('/tmp/dvm')/a.tag
    if trial.exists():p.error('trial already exists')
    def run(name,*args,**kw):return subprocess.run([sys.executable,str(src/name),*map(str,args)],check=True,**kw)
    log=trial.with_suffix('.matrix-run.log').open('x')
    proc=subprocess.Popen([sys.executable,str(src/'run_guest_load.py'),str(a.manifest),'--tag',a.tag,'--seconds','600',
        '--driver-mmio','--driver-present','--driver-consumer','--driver-runner','--driver-wait-display',
        '--driver-worker',str(a.worker),'--library-cache',str(a.library)],stdout=log,stderr=subprocess.STDOUT,cwd=repo)
    result=dict(plan=str(a.plan.resolve()),manifest=str(a.manifest.resolve()),jobs=[],passed=False)
    started=time.monotonic();last_shared=None
    try:
        while not (trial/'runner-inbox').is_dir():
            if proc.poll() is not None or time.monotonic()-started>300:raise RuntimeError('runner inbox unavailable')
            time.sleep(.2)
        for index,row in enumerate(plan):
            args=[trial,'--bundle',row['bundle'],'--worker',row['worker'],'--mode',row.get('mode','data')]
            shared=row.get('mode')!='installed'
            if shared:args+=['--development','--test','package','--frames',row['frames'],'--hz',row['hz'],'--shared-surface','--surface-handoff']
            queued=json.loads(run('runner_control.py',*args,capture_output=True,text=True).stdout)
            job=queued['job'];directory=trial/'runner-jobs'/str(job)
            result['jobs'].append(dict(job=job,label=row.get('label',str(index)),verified=False))
            atomic_json(trial/'matrix.json',result);print(f'queued {job}: {row.get("label",index)}',flush=True)
            deadline=started+340 if index==0 else time.monotonic()+100
            while True:
                if (directory/'result.json').exists():
                    try:r=json.loads((directory/'result.json').read_text())
                    except json.JSONDecodeError:r=None
                    if r is not None:break
                if proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError(f'job {job} did not finish')
                time.sleep(.2)
            if not r.get('verified'):raise RuntimeError(f'job {job} failed; no shared reuse')
            evidence=verify(directory);atomic_json(directory/'independent-verification.json',evidence)
            result['jobs'][-1].update(verified=True,guest_pid=evidence['guest_pid'])
            atomic_json(trial/'matrix.json',result);print(f'verified {job}',flush=True)
            if shared:last_shared=directory
        # Observe recovery before native Home, after the installed control.
        with (trial/'matrix-recovery.log').open('x') as recovery_log:
            observer=subprocess.Popen([sys.executable,str(src/'verify_runner_display.py'),str(trial),
                '--after-job',str(job),'--label','matrixWake'],stdout=recovery_log,stderr=subprocess.STDOUT)
            try:
                for attempt in range(2):
                    prefix=trial/f'matrix-wake-{attempt+1}'
                    with (trial/f'matrix-input-{attempt+1}.log').open('x') as input_log:
                        subprocess.run([sys.executable,str(repo/'tools/input/native_input.py'),'--run',str(trial),
                            '--frames',str(prefix),'--frame-wait','2','--settle','2','--log',str(prefix)+'.json','home'],check=True,
                            stdout=input_log,stderr=subprocess.STDOUT,timeout=20)
                    outcome=json.loads(Path(str(prefix)+'.json').read_text())
                    if outcome.get('ok') and outcome.get('frame_changed'):break
                if observer.wait(timeout=35):raise RuntimeError('native display/input recovery failed')
            finally:
                if observer.poll() is None:observer.terminate();observer.wait(timeout=5)
        result['display_recovery_verified']=True
    except BaseException as e:
        result['failure']=repr(e)
        raise
    finally:
        if proc.poll() is None:
            try:run('runner_control.py',trial,'--stop',capture_output=True,text=True)
            except Exception:proc.send_signal(signal.SIGINT)
            try:proc.wait(timeout=40)
            except subprocess.TimeoutExpired:proc.send_signal(signal.SIGINT);proc.wait(timeout=20)
        log.close()
        if trial.exists():atomic_json(trial/'matrix.json',result)
    if proc.returncode:raise RuntimeError('owned runner did not stop successfully')
    result['scanout']=verify_scanout_export(trial,last_shared)
    result['passed']=True;result['elapsed_seconds']=time.monotonic()-started
    atomic_json(trial/'matrix.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':main()
