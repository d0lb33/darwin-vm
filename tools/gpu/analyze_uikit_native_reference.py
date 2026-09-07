#!/usr/bin/env python3
"""Verify guest UIKit against native composition of its captured glyph inputs."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from analyze_uikit_capture import analyze
from analyze_uikit_sampling import analyze as sampling


def compare(job,native):
    guest=analyze(job)  # Reconstruct output from checked guest audit/RPC capture.
    meta=json.loads((native/'manifest.json').read_text())
    if meta.get('scope')!='host-catalyst-rehearsal-not-exact-guest' or meta.get('forwarded') or meta.get('native_contract_override') or meta.get('native_air_override') or meta.get('exit')!=0 or meta.get('frames')!=1 or meta.get('animate'):
        raise ValueError('requires default native Metal composition, one frame, no capability/library substitution')
    inputs=meta.get('guest_rasters',{})
    if inputs.get('source_job')!=str(job.resolve()):raise ValueError('native input source job differs')
    predicted=sampling(job)
    for key in ('source_sha256','bounds','input_color_rgba'):
        if [x[key] for x in inputs['labels']]!=[x[key] for x in predicted['labels']]:raise ValueError('native glyph inputs differ from guest: '+key)
    pixels=(native/'gpu.bgra').read_bytes()
    if hashlib.sha256(pixels).hexdigest()!=meta.get('gpu_sha256'):raise ValueError('native output hash')
    actual=(job/'uikit-gpu.bgra').read_bytes()
    if len(pixels)!=len(actual):raise ValueError('native output extent')
    differences=[abs(a-b) for a,b in zip(pixels,actual)]
    lines=[json.loads(x)['line'] for x in (job/'driver-audit.jsonl').read_text().splitlines()]
    scales=[float(re.search(r' scale=([0-9.]+)',line)[1]) for line in lines if line.startswith('GPU_LOAD_UIKIT_LAYER stage=after-display ')]
    curves=[re.search(r' corner_curve=(\w+)',line)[1] for line in lines if line.startswith('GPU_LOAD_UIKIT_GEOMETRY stage=after-display ')]
    if inputs.get('layer_scales')!=scales or inputs.get('layer_corner_curves')!=curves:raise ValueError('native layer metadata differs from guest')
    native_log=(native/'result.log').read_text()
    if 'UIKIT_HOST_NATIVE_LIBRARY requested=' not in native_log or 'substitution=0' not in native_log or 'UIKIT_HOST_FRAME index=0 completed=1' not in native_log:
        raise ValueError('native execution witnesses absent')
    pending='GPU_LOAD_UIKIT_REFERENCE mode=external-native status=pending' in lines
    complete='GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0' in lines
    match=all(d<=2 for d in differences)
    return dict(scope='actual-guest-UIKit-offscreen-output-versus-independent-native-composition-of-guest-inputs; not display/system compositor',
        verified=match and guest['process_success'] and guest['resources_retired'] and pending and complete,
        pixel_reference='native host UIKit image composition with exact guest A8 glyphs, color, scale and corner-curve metadata',
        pixels_match=match,channels_over_2=sum(d>2 for d in differences),max_error=max(differences),mean_error=sum(differences)/len(differences),
        process_success=guest['process_success'],resources_retired=guest['resources_retired'],external_reference_capture=pending,
        guest_gpu_sha256=guest['gpu_sha256'],native_gpu_sha256=meta['gpu_sha256'],
        original_guest_cpu_reference=guest,display_verified=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('job',type=Path);p.add_argument('native',type=Path)
    args=p.parse_args();result=compare(args.job,args.native)
    (args.job/'uikit-native-reference.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    raise SystemExit(0 if result['verified'] else 1)
