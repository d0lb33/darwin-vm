#!/usr/bin/env python3
"""Preserve long runtime traces losslessly without copying disk/DRAM artifacts."""
import argparse,gzip,hashlib,json,shutil
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('trial',type=Path);a=p.parse_args()
if not (a.trial/'result.json').is_file():raise ValueError('trial must be stopped with a result')
records=[]
for name in ('stderr.log','serial.log','wire.log'):
    source=a.trial/name
    if not source.is_file():continue
    destination=source.with_name(name+'.gz')
    with source.open('rb') as reader,destination.open('xb') as writer:
        with gzip.GzipFile(filename='',mode='wb',fileobj=writer,mtime=0) as compressed:shutil.copyfileobj(reader,compressed,1024*1024)
    digest=hashlib.sha256();size=0
    with gzip.open(destination,'rb') as reader:
        for data in iter(lambda:reader.read(1024*1024),b''):digest.update(data);size+=len(data)
    with source.open('rb') as reader:original=hashlib.file_digest(reader,'sha256').hexdigest()
    if size!=source.stat().st_size or digest.hexdigest()!=original:raise ValueError('lossless log verification failed')
    with destination.open('rb') as reader:compressed_hash=hashlib.file_digest(reader,'sha256').hexdigest()
    records.append(dict(source=name,bytes=size,sha256=original,compressed=destination.name,compressed_sha256=compressed_hash))
(a.trial/'compressed-logs.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps(records))
