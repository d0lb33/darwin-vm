"""Recover the exact logical render request from captured staging transactions.

Keep wire captures unchanged. Pixel verifiers use the committed request only
once its size, digest, token, chunk order and successful completion agree.
"""
import base64
import hashlib
import json


def expand_render_staging(records):
    stage=None
    expanded=[]
    for row in records:
        op=row['op'];request=row['request'];reply=row['reply']
        if op=='renderStageBegin' and reply.get('ok'):
            if stage is not None or not 0<request['length']<=2*1024*1024:
                raise ValueError('staging capture begin extent/ownership')
            if reply.get('offset')!=0 or type(reply.get('token')) is not int or reply['token']<=0:
                raise ValueError('staging capture begin acknowledgement')
            stage=dict(token=reply['token'],length=request['length'],sha=request['sha256'],data=bytearray())
        elif op=='renderStageChunk' and reply.get('ok'):
            if stage is None or request['token']!=stage['token'] or reply.get('token')!=stage['token']:
                raise ValueError('staging capture chunk token')
            raw=base64.b64decode(request['payload'],validate=True)
            if not 0<len(raw)<=32768 or request['offset']!=len(stage['data']) or len(stage['data'])+len(raw)>stage['length']:
                raise ValueError('staging capture chunk extent/order')
            stage['data'].extend(raw)
            if reply.get('offset')!=len(stage['data']):raise ValueError('staging capture chunk acknowledgement')
        elif op=='renderStageAbort' and reply.get('ok'):
            if stage is None or request['token']!=stage['token'] or reply.get('token')!=stage['token']:
                raise ValueError('staging capture abort token')
            stage=None
        elif op=='renderStageCommit' and reply.get('ok'):
            if stage is None or request['token']!=stage['token']:raise ValueError('staging capture commit token')
            if len(stage['data'])!=stage['length'] or hashlib.sha256(stage['data']).hexdigest()!=stage['sha']:
                raise ValueError('staging capture complete bytes/hash')
            if reply.get('stagedBytes')!=stage['length'] or reply.get('stagedSHA256')!=stage['sha'] or reply.get('status')!=4:
                raise ValueError('staging capture completion identity/status')
            decoded=json.loads(stage['data'])
            if not isinstance(decoded,dict) or decoded.get('op')!='renderSubmit' or 'seq' in decoded:
                raise ValueError('staging capture logical operation')
            row={**row,'op':'renderSubmit','request':{**decoded,'seq':row['seq']},'staged_wire_op':op}
            stage=None
        expanded.append(row)
    if stage is not None:raise ValueError('incomplete staging capture')
    return expanded
