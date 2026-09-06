#!/usr/bin/env python3
"""Summarize sparse, sequential SplashBoard encoding callbacks (not app installs).

Start with the first complete entry/return pair; exclude an encoding already
in progress at restore. Refuse overlapping calls because CPU identity cannot
pair a guest thread which migrates between vCPUs. Compare the same checkpoint,
configuration and first N images. No timing estimate for incomplete calls.
"""
import argparse,json,statistics
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('events',type=Path)
p.add_argument('--count',type=int)
a=p.parse_args();rows=[];pending=None;first=None
for line in a.events.read_text().splitlines():
 fields=line.split('\t');when=float(fields[0]);name=fields[2]
 if name=='SNAPSHOT_ENCODE_ENTRY':
  if pending is not None:raise RuntimeError('overlapping calls: require guest thread IDs')
  pending=(when,int(fields[6],16));first=when if first is None else first
 elif name=='SNAPSHOT_ENCODE_RETURN' and pending is not None:
  if int(fields[3],16)==0:raise RuntimeError('encoding returned nil')
  rows.append(dict(seconds=when-pending[0],format=pending[1],finished=when));pending=None
  if a.count and len(rows)>=a.count:break
if not rows:raise RuntimeError('no complete successful encodings')
if a.count and len(rows)<a.count:raise RuntimeError('not enough complete encodings')
print(json.dumps(dict(count=len(rows),formats=sorted(set(r['format'] for r in rows)),
 median_seconds=statistics.median(r['seconds'] for r in rows),
 encoding_seconds=sum(r['seconds'] for r in rows),
 first_entry_to_last_return_seconds=rows[-1]['finished']-first),indent=2))
