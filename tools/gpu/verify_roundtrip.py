"""Verify the captured byte streams, GPU witnesses and guest verification report."""
import hashlib
import io
import json
from pathlib import Path
SHA = '8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364'

def require(condition, detail='verification failed'):
    if not condition:
        raise ValueError(detail)

def verify(folder):
    folder = Path(folder)
    request = (folder / 'guest-requests.bin').read_bytes()
    response = (folder / 'host-responses.bin').read_bytes()
    q = io.BytesIO(request)
    r = io.BytesIO(response)
    require(q.readline() == f'LIBREF 1 2705796 {SHA}\n'.encode(), "q.readline()==f'LIBREF 1 2705796 {SHA}\\n'.encode()")
    require(r.readline() == b'OK 1\n', "r.readline()==b'OK 1\\n'")
    require(q.readline() == b'PIPE 2 read_write_surf_compute\n' and r.readline() == b'OK 2\n', "q.readline()==b'PIPE 2 read_write_surf_compute\\n' and r.readline()==b'OK 2\\n'")
    checks = []
    for generation in range(3):
        for step in range(3):
            ident = 3 + generation * 3 + step
            delay = 200 if ident == 3 else 0
            require(q.readline() == f'RUN {ident} {generation + 1} 64 48 {delay} 12288\n'.encode(), "q.readline()==f'RUN {ident} {generation+1} 64 48 {delay} 12288\\n'.encode()")
            source = q.read(12288)
            expected = bytearray()
            for y in range(48):
                for x in range(64):
                    expected.extend((x * 17 + y * 31 + generation * 43 + step * 59 & 255, x * 7 + y * 13 + generation * 19 + step * 29 & 255, (x ^ y * 3 ^ generation * 97 ^ step * 71) & 255, 255 - x - y - generation * 11 - step * 5 & 255))
            require(source == expected, 'source==expected')
            require(r.readline() == f'DATA {ident} 12288\n'.encode(), "r.readline()==f'DATA {ident} 12288\\n'.encode()")
            output = r.read(12288)
            require(output == source, 'output==source')
            checks.append(dict(id=ident, bytes=len(output), sha256=hashlib.sha256(output).hexdigest()))
    head = q.readline().split()
    require(len(head) == 3 and head[:2] == [b'REPORT', b'12'] and (0 < int(head[2]) <= 16384), 'invalid verification report header/length')
    report = json.loads(q.read(int(head[2])))
    require(q.read() == b'' and r.readline() == b'OK 12\n' and (r.read() == b''), "q.read()==b'' and r.readline()==b'OK 12\\n' and r.read()==b''")
    require(report['passed'] is True and report['air_sha256'] == SHA and (report['air_bytes'] == 2705796), "report['passed'] is True and report['air_sha256']==SHA and report['air_bytes']==2705796")
    require(len(report['runs']) == 9, "len(report['runs'])==9")
    for i, run in enumerate(report['runs']):
        require(run['generation'] == i // 3 and run['pass'] == i % 3, "run['generation']==i//3 and run['pass']==i%3")
        require(run['pre_wait_status'] == 2 and run['status'] == 4, "run['pre_wait_status']==2 and run['status']==4")
        require(run['direct_surface_equal'] is True and run['early_read_rejected'] is True, "run['direct_surface_equal'] is True and run['early_read_rejected'] is True")
    logs = (folder / 'host-worker.stderr').read_text().splitlines()
    gpu = [x for x in logs if x.startswith('DIAG event=run ')]
    require(len(gpu) == 9, 'len(gpu)==9')
    for i, line in enumerate(gpu):
        require(f'id={i + 3} generation={i // 3 + 1} detail=status=4 ' in line and 'pre_dispatch_differs=1' in line, "f'id={i+3} generation={i//3+1} detail=status=4 ' in line and 'pre_dispatch_differs=1' in line")
    reports = [x for x in logs if x.startswith('DIAG event=guest-verification ')]
    require(len(reports) == 1 and json.loads(reports[0].split(' detail=', 1)[1]) == report, "len(reports)==1 and json.loads(reports[0].split(' detail=',1)[1])==report")
    return dict(passed=True, guest_report=report, operations=checks, request_bytes=len(request), response_bytes=len(response), request_sha256=hashlib.sha256(request).hexdigest(), response_sha256=hashlib.sha256(response).hexdigest())


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    print(json.dumps(verify(parser.parse_args().folder), indent=2))
