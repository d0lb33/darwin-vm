#!/usr/bin/env python3
"""Predict bounded UIKit text/image pixels from guest inputs using CPU math.

The fixture is consumer_uikit_scene.inc at 320x480. This diagnoses the reference
contract; it does not promote a failed guest process or certify rounded edges.
"""
import argparse
import base64
import hashlib
import json
import math
import re
from pathlib import Path
import struct


def analyze(job,export=None):
    rows=[json.loads(x) for x in (job/'driver-host.jsonl').read_text().splitlines()]
    allocations={};textures={};buffers={};labels=[];functions={};pipelines={}
    for row in rows:
        request=row['request']
        if 'upload_file' in row:
            raw=(job/row['upload_file']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row['upload_sha256']:raise ValueError('upload capture hash')
            request=json.loads(raw)
            if {k:v for k,v in request.items() if k!='data'}!=row['request']:raise ValueError('upload metadata')
        if row['op']=='texture':allocations[row['reply']['handle']]=request
        if row['op']=='function':functions[row['reply']['handle']]={c['key']:base64.b64decode(c['data'],validate=True) for c in request['constants']}
        if row['op']=='renderPipeline':pipelines[row['reply']['handle']]=functions.get(request.get('fragmentHandle'),{})
        if row['op']=='writeTextureChunk':
            data=base64.b64decode(request['data'],validate=True);target=textures.setdefault(request['texture'],bytearray())
            if request['offset']!=len(target):raise ValueError('fixture texture chunk order')
            target.extend(data)
        if row['op']=='writeRenderBuffer':
            data=base64.b64decode(request['data'],validate=True);target=buffers.setdefault(request['buffer'],bytearray(1024*1024))
            start=request['offset']
            if start+len(data)>len(target):raise ValueError('buffer bounds')
            target[start:start+len(data)]=data
        if row['op']!='renderSubmit':continue
        if row['reply'].get('status')!=4:raise ValueError('GPU submission did not complete')
        for upload in request['uploads']:textures[upload['texture']]=base64.b64decode(upload['data'],validate=True)
        for render_pass in request['commands']:
            vertex={};fragment={};constants={}
            for op in render_pass['operations']:
                if op[0]=='pipeline':constants=pipelines[op[1]]
                if op[0]=='vertexBuffer':vertex[op[3]]=(op[1],op[2])
                if op[0]=='fragmentTexture':fragment[op[2]]=op[1]
                # Exact captured specializations use image function 25 for A8
                # sampling. Other draws may leave an unused A8 binding set.
                if op[0]!='indexed' or constants.get('fc_image_function0')!=b'\x19' or allocations.get(fragment.get(3),{}).get('format')!=1:continue
                handle=fragment[3];descriptor=allocations[handle]
                if op[2]!=6 or 1 not in vertex:raise ValueError('A8 quad contract')
                buffer,offset=vertex[1];raw=buffers[buffer]
                points=[struct.unpack_from('<6f',raw,offset+i*48) for i in range(4)]
                color=struct.unpack_from('<4e',raw,offset+32)
                if any(struct.unpack_from('<4e',raw,offset+i*48+32)!=color for i in range(4)):raise ValueError('nonuniform text color')
                x,y=points[0][:2];w=points[1][0]-x;h=points[2][1]-y
                if points!=[(x,y,0.,1.,0.,0.),(x+w,y,0.,1.,1.,0.),(x+w,y+h,0.,1.,1.,1.),(x,y+h,0.,1.,0.,1.)]:raise ValueError('quad layout/UV')
                if any(v!=int(v) for v in (x,y,w,h)) or descriptor['width']!=w*2 or descriptor['height']!=h*2:raise ValueError('requires captured 2x integer-aligned fixture text')
                if len(textures[handle])!=w*h*4:raise ValueError('A8 input extent')
                labels.append((handle,int(x),int(y),int(w),int(h),color,bytes(textures[handle])))
    wanted={(20,22,280,46):(51,31,20),(36,242,252,30):(166,77,38),(85,345,150,18):(102,153,26)}
    if {(x,y,w,h) for _,x,y,w,h,_,_ in labels}!=set(wanted) or len(labels)!=3:raise ValueError('fixture label geometry')
    gpu=(job/'uikit-gpu.bgra').read_bytes();cpu=(job/'uikit-cpu.bgra').read_bytes()
    if len(gpu)!=320*480*4 or len(cpu)!=len(gpu):raise ValueError('output extent')
    reference=bytearray(cpu);reports=[]
    if export:export.mkdir(exist_ok=False)
    for handle,x,y,w,h,color,data in labels:
        background=wanted[x,y,w,h];foreground=[color[i]*255 for i in (2,1,0)]
        deltas=[]
        for py in range(h):
            for px in range(w):
                alpha=sum(data[(py*2+dy)*w*2+px*2+dx] for dy in range(2) for dx in range(2))/1020
                predicted=bytes([round(b+(c-b)*alpha) for b,c in zip(background,foreground)]+[255])
                offset=((y+py)*320+x+px)*4;reference[offset:offset+4]=predicted
                deltas.extend(abs(a-b) for a,b in zip(predicted,gpu[offset:offset+4]))
        reports.append(dict(texture=handle,bounds=[x,y,x+w,y+h],source_sha256=hashlib.sha256(data).hexdigest(),
            input_color_rgba=list(color),channels_over_2=sum(d>2 for d in deltas),max_error=max(deltas)))
        if export:
            name={(20,22):'title',(36,242):'caption',(85,345):'button'}[x,y]
            (export/(name+'.a8')).write_bytes(data)
            reports[-1].update(name=name,width=w*2,height=h*2)
    if export:
        audit=[json.loads(x)['line'] for x in (job/'driver-audit.jsonl').read_text().splitlines()]
        scales=[float(re.search(r' scale=([0-9.]+)',line)[1]) for line in audit if line.startswith('GPU_LOAD_UIKIT_LAYER stage=after-display ')]
        curves=[re.search(r' corner_curve=(\w+)',line)[1] for line in audit if line.startswith('GPU_LOAD_UIKIT_GEOMETRY stage=after-display ')]
        if len(scales)!=7 or len(curves)!=7 or any(c not in ('circular','continuous') for c in curves):raise ValueError('captured fixture layer metadata')
        # Export only inputs. The reference producer must not consume either
        # final-image bytes or the diagnostic comparisons against those bytes.
        fields=('texture','bounds','source_sha256','input_color_rgba','name','width','height')
        exported=[{k:label[k] for k in fields} for label in reports]
        (export/'inputs.json').write_text(json.dumps(dict(scope='exact-guest-generated-glyph-inputs-not-rendered-output',source_job=str(job.resolve()),layer_scales=scales,layer_corner_curves=curves,labels=exported),indent=2)+'\n')
    # Known opaque checker contents, bilinear minification/magnification. Keep
    # the original CPU reference at partially covered rounded-clip boundaries.
    colors=((32,128,240,255),(240,208,32,255));deltas=[];count=0
    for y in range(108,236):
        for x in range(20,300):
            if y<112 and (x<44 or x>=276) and math.hypot(x+.5-(44 if x<44 else 276),y+.5-112)>23:continue
            sx=(x+.5-8)*16/304-.5;sy=(y+.5-108)*16/128-.5
            ix=math.floor(sx);iy=math.floor(sy);dx=sx-ix;dy=sy-iy
            predicted=bytes(round(sum(colors[(max(0,min(15,ix+i))//4+max(0,min(15,iy+j))//4)%2][c]*(dx if i else 1-dx)*(dy if j else 1-dy)
                for i in range(2) for j in range(2))) for c in range(4))
            offset=(y*320+x)*4;reference[offset:offset+4]=predicted;count+=1
            deltas.extend(abs(a-b) for a,b in zip(predicted,gpu[offset:offset+4]))
    residual=[abs(a-b) for a,b in zip(reference,gpu)]
    return dict(scope='CPU sampling prediction from exact guest inputs; diagnostic only, no whole-scene acceptance',
        labels=reports,circular_clip_hypothesis_pixels=count,circular_clip_hypothesis_channels_over_2=sum(d>2 for d in deltas),circular_clip_hypothesis_max_error=max(deltas),
        hybrid_residual_channels_over_2=sum(d>2 for d in residual),hybrid_residual_max_error=max(residual),
        original_cpu_reference_preserved=True,guest_process_verdict_unchanged=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('job',type=Path)
    parser.add_argument('--export-glyphs',type=Path,help='write original A8 inputs and color/geometry metadata for a native composition control')
    args=parser.parse_args();result=analyze(args.job,args.export_glyphs)
    (args.job/'uikit-sampling-analysis.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
