"""Host readiness gate. The reviewer observes our VM image; never other VMs.

Input ACK is protocol liveness, not proof every UI service is healthy. The
review record binds a visible home screen to this session and image. All
actions occur before timing starts; an input restart invalidates readiness.
"""
import json
import re
from checkpoint_common import HMP, atomic_json, sha256


class AuxReady:
    def __init__(self,out,peer,report):
        self.out,self.peer,self.report=out,peer,report
        self.ack=None
        self.pending=None
        self.counter=910000
        self.candidate=None
        self.approved=None
        self.released_at=None
        self.waiting=False
        self.capture_number=0
        self.events=report.setdefault('readiness_events',[])

    def sync(self,wire,now,kind):
        self.counter+=1
        self.pending=(self.counter,kind)
        packet=f'\nDVMINPUT1 {self.counter} S 0 0 0\n'.encode()
        if wire.send(packet)!=len(packet):raise RuntimeError('short readiness sync write')
        self.events.append(dict(seconds=now,event='sync_sent',sequence=self.counter,kind=kind))

    def feed(self,line,wire,now):
        if 'GPU_LOAD_AUX_WAIT ' in line:
            self.waiting=True
            self.events.append(dict(seconds=now,event='guest_wait'))
        if 'DVM_INPUT_START ' in line:
            if self.released_at is not None:raise RuntimeError('input restarted during latency batch')
            self.ack=self.pending=self.candidate=self.approved=None
            self.events.append(dict(seconds=now,event='input_start'))
        if 'DVM_INPUT_READY ' in line:
            if self.released_at is not None:raise RuntimeError('input reinitialized during latency batch')
            self.ack=self.candidate=self.approved=None
            self.events.append(dict(seconds=now,event='input_ready'))
            self.sync(wire,now,'settle')
        if self.pending and re.search(r'\bDVM_INPUT_ACK '+str(self.pending[0])+r' 1(?:\s|$)',line):
            sequence,kind=self.pending
            self.pending=None
            self.events.append(dict(seconds=now,event='sync_ack',sequence=sequence,kind=kind))
            if kind=='settle':self.ack=now
            else:
                if not self.approved:raise RuntimeError('release ACK without image review')
                if now>=300:raise TimeoutError('release ACK missed readiness deadline')
                self.peer.release();self.released_at=now
                self.report['readiness_release']=dict(seconds=now,review=self.approved,
                    review_file=self.candidate['review_file'],
                    review_sha256=sha256(self.out/self.candidate['review_file']))
                self.events.append(dict(seconds=now,event='released'))

    def tick(self,wire,now):
        if self.released_at is not None:return
        if now>=300:raise TimeoutError('home-screen readiness not verified within 300 seconds')
        if not self.waiting or self.ack is None or now-self.ack<15:return
        if self.candidate is None:
            self.capture_number+=1
            image=self.out/f'ready-{self.capture_number}.png'
            HMP(self.out/'monitor.sock',timeout=5).command(f'screendump {image} -f png')
            self.candidate=dict(image=image.name,image_sha256=sha256(image),
                session=self.peer.header.hex(),input_ack_seconds=self.ack,
                source_manifest_sha256=self.report.get('source_manifest_sha256'),
                capture_seconds=now,review_file=f'review-{self.capture_number}.json')
            atomic_json(self.out/f'ready-{self.capture_number}.json',self.candidate)
            self.events.append(dict(seconds=now,event='image_ready',**self.candidate))
            print('GPU_POSTBOOT_REVIEW '+json.dumps(self.candidate),flush=True)
        review_path=self.out/self.candidate['review_file']
        if not review_path.exists() or self.approved:return
        review=json.loads(review_path.read_text())
        for key in ('image','image_sha256','session','source_manifest_sha256'):
            if review.get(key)!=self.candidate[key]:raise ValueError('stale/wrong readiness review')
        if sha256(self.out/review['image'])!=review['image_sha256']:
            raise ValueError('reviewed screenshot changed')
        if review.get('home_visible') is not True:
            self.events.append(dict(seconds=now,event='image_rejected',review=review))
            self.candidate=None;self.ack=None
            self.sync(wire,now,'settle')
            return
        if now-self.candidate['capture_seconds']>60:
            raise TimeoutError('home-screen image review exceeded 60 seconds')
        self.approved=review
        self.events.append(dict(seconds=now,event='image_approved',review=review,
            review_file=self.candidate['review_file'],review_sha256=sha256(review_path)))
        self.sync(wire,now,'release')
