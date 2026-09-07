#!/usr/bin/env python3
"""Preserve compositor provenance across an additional isolated service install."""
import argparse,json
from pathlib import Path
def main():
    p=argparse.ArgumentParser();p.add_argument('original',type=Path);p.add_argument('installed',type=Path);p.add_argument('out',type=Path);a=p.parse_args()
    original=json.loads(a.original.read_text());installed=json.loads(a.installed.read_text())
    if installed['disk']['backing_chain'][1:]!=original['disk']['backing_chain']:
        raise ValueError('installation must derive directly from the original pinned disk chain')
    if not original['guest_installation']['scope'].startswith('backboardd-only boot registration'):
        raise ValueError('requires existing compositor installation')
    installed['cellular_service_installation']=installed['guest_installation']
    installed['guest_installation']=original['guest_installation']
    with a.out.open('x') as f:json.dump(installed,f,indent=2)
if __name__=='__main__':main()
