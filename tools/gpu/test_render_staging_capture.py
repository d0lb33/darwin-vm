import base64
import copy
import hashlib
import json
import unittest
from render_staging_capture import expand_render_staging


class CaptureTests(unittest.TestCase):
    def fixture(self):
        data=json.dumps(dict(op='renderSubmit',commands=[],uploads=[],readbacks=[])).encode()
        sha=hashlib.sha256(data).hexdigest()
        return [dict(seq=1,op='renderStageBegin',request=dict(length=len(data),sha256=sha),reply=dict(ok=True,token=7,offset=0)),
                dict(seq=2,op='renderStageChunk',request=dict(token=7,offset=0,payload=base64.b64encode(data).decode()),reply=dict(ok=True,token=7,offset=len(data))),
                dict(seq=3,op='renderStageCommit',request=dict(token=7),reply=dict(ok=True,status=4,stagedBytes=len(data),stagedSHA256=sha))]

    def test_preserves_capture_and_recovers_only_completed_request(self):
        rows=self.fixture();original=copy.deepcopy(rows);expanded=expand_render_staging(rows)
        self.assertEqual(rows,original)
        self.assertEqual(expanded[-1]['op'],'renderSubmit')
        self.assertEqual(expanded[-1]['request'],dict(seq=3,op='renderSubmit',commands=[],uploads=[],readbacks=[]))
        self.assertEqual(expanded[-1]['reply'],rows[-1]['reply'])
        self.assertEqual(expanded[-1]['staged_wire_op'],'renderStageCommit')

    def test_rejects_missing_corrupt_reordered_or_uncompleted_work(self):
        changes=[(1,'request','token',8),(1,'request','offset',1),(1,'reply','offset',0),
                 (1,'request','payload','eA=='),(2,'reply','stagedSHA256','0'*64),
                 (2,'reply','stagedBytes',1),(2,'reply','status',5),(2,'reply','ok',False)]
        for index,part,key,value in changes:
            rows=self.fixture();rows[index][part][key]=value
            with self.subTest(change=(index,part,key,value)),self.assertRaises(ValueError):expand_render_staging(rows)
        with self.assertRaises(ValueError):expand_render_staging(self.fixture()[:-1])
        with self.assertRaises(ValueError):expand_render_staging([self.fixture()[0],self.fixture()[2]])

    def test_abort_produces_no_logical_submission(self):
        rows=self.fixture()[:2];rows.append(dict(seq=3,op='renderStageAbort',request=dict(token=7),reply=dict(ok=True,token=7)))
        self.assertFalse(any(r['op']=='renderSubmit' for r in expand_render_staging(rows)))
