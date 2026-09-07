"""Full-screen reference check, additional to shared ownership/completion checks."""
import base64
import hashlib
import json
from pathlib import Path
from analyze_uikit_capture import write_png


def verify_reference(directory, lines, records, frames, backing):
    directory=Path(directory)
    pinned=json.loads((directory/'job.json').read_text())['uikit_reference']
    files={}
    for name,key in (('gpu.bgra','gpu_sha256'),('manifest.json','manifest_sha256'),('result.log','log_sha256')):
        data=(directory/('reference-'+name)).read_bytes()
        if hashlib.sha256(data).hexdigest()!=pinned[key]:raise ValueError('native reference hash: '+name)
        files[name]=data
    meta=json.loads(files['manifest.json'])
    if (meta.get('scope')!='host-catalyst-rehearsal-not-exact-guest' or meta.get('forwarded') or
        meta.get('native_air_override') or meta.get('native_contract_override') or meta.get('animate') or
        meta.get('frames')!=1 or meta.get('display_frame')!=frames or meta.get('exit')!=0 or
        (meta.get('width'),meta.get('height'))!=(1179,2556)):
        raise ValueError('native display reference mode')
    if meta.get('gpu_sha256')!=pinned['gpu_sha256']:raise ValueError('native rendered image hash')
    log=files['result.log'].decode()
    if 'UIKIT_HOST_FRAME index=0 completed=1' not in log or 'substitution=0' not in log:
        raise ValueError('native execution witness')
    # References may reuse settled glyph inputs, but the current guest must
    # independently produce the same A8 images. Never substitute host fonts.
    allocations={};uploads={}
    for row in records:
        request=row['request']
        if 'upload_file' in row:
            raw=(directory/row['upload_file']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row['upload_sha256']:raise ValueError('glyph upload hash')
            request=json.loads(raw)
            if {k:v for k,v in request.items() if k!='data'}!=row['request']:raise ValueError('glyph upload metadata')
        if row['op']=='texture' and row['reply'].get('ok'):allocations[row['reply']['handle']]=request
        if row['op']=='renderSubmit':
            for upload in request['uploads']:
                uploads[upload['texture']]=bytearray(base64.b64decode(upload['data'],validate=True))
        if row['op']=='writeTextureChunk':
            data=base64.b64decode(request['data'],validate=True)
            target=uploads.setdefault(request['texture'],bytearray())
            if request['offset']!=len(target):raise ValueError('glyph chunk sequence')
            target.extend(data)
    labels=meta.get('guest_rasters',{}).get('labels',[])
    if len(labels)!=3 or {x['name'] for x in labels}!={'title','caption','button'}:raise ValueError('native glyph provenance')
    actual_inputs={(d['width'],d['height'],hashlib.sha256(uploads.get(h,b'')).hexdigest())
                   for h,d in allocations.items() if d.get('format')==1}
    expected_inputs={(x['width'],x['height'],x['source_sha256']) for x in labels}
    if actual_inputs!=expected_inputs:raise ValueError('displayed guest glyph inputs differ from reference')
    # The backing has a padded row; the native reference is tightly packed.
    pixels=b''.join(backing[y*4864:y*4864+1179*4] for y in range(2556))
    reference=files['gpu.bgra']
    if len(pixels)!=1179*2556*4 or len(reference)!=len(pixels):raise ValueError('display reference extent')
    write_png(directory/'uikit-shared.png',pixels,1179,2556)
    differences=[abs(a-b) for a,b in zip(pixels,reference)]
    result=dict(verified=all(d<=2 for d in differences),channels_over_2=sum(d>2 for d in differences),
                max_error=max(differences),mean_error=sum(differences)/len(differences),
                gpu_sha256=hashlib.sha256(pixels).hexdigest(),native_sha256=pinned['gpu_sha256'],
                scope='final guest shared IOSurface versus independent native UIKit composition; DCP bytes checked separately')
    (directory/'uikit-display-reference.json').write_text(json.dumps(result,indent=2)+'\n')
    if not result['verified']:raise ValueError('UIKit full-screen native pixel comparison: '+json.dumps(result))
    return result
