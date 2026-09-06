#!/usr/bin/env python3
"""Run an owned, paused migration replay without a debugger; stop on SEP rejection.

Times are active wall seconds after HMP cont, excluding restore/capture time.
Device events are work witnesses, not counts of successfully installed apps.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP


def is_sep_no_reply(line):
    return line.startswith('sep(') and ('no reply' in line or 'rejected unsupported' in line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--monitor', required=True)
    parser.add_argument('--seconds', type=float, default=180)
    parser.add_argument('--sample-after', type=float)
    parser.add_argument('--scan-limit', type=int, default=0,
                        help='stop after this many successful native LS_SCAN_RETURN events')
    parser.add_argument('--ignore-sep', action='store_true', help='observe failure paths without stopping on unanswered SEP requests')
    parser.add_argument('--stop-serial', help='stop on this literal serial marker')
    args = parser.parse_args()
    run = args.run
    hmp = HMP(Path(args.monitor))
    if 'paused' not in hmp.command('info status'):
        raise RuntimeError('timing must begin from a paused VM')
    pid = int((run / 'qemu.pid').read_text())
    events = []
    result = {'pid': pid, 'started_unix': time.time(), 'events': events,
              'debugger_connected': False, 'reason': 'active-time-limit'}
    serial = (run / 'serial.log').open('rb') if args.stop_serial else None
    serial_tail = b''
    sample = None
    sample_log = None
    completed_scans = 0
    milestones = (run / 'milestones.tsv').open() if args.scan_limit else None
    with (run / 'qemu.stderr.log').open('rb') as stream:
        stream.seek(0, 2)
        result['stderr_start_offset'] = stream.tell()
        tail = b''
        start = time.monotonic()
        result['started_monotonic'] = start
        hmp.command('cont')
        try:
            while time.monotonic() - start < args.seconds:
                elapsed = time.monotonic() - start
                data = tail + stream.read()
                lines = data.split(b'\n')
                tail = lines.pop()
                rejected = False
                for raw in lines:
                    line = raw.decode('utf-8', 'replace')
                    if re.search(r'sks op0f|rejected unsupported|no reply|presented ', line):
                        events.append({'seconds': round(elapsed, 3), 'line': line})
                    if not args.ignore_sep and is_sep_no_reply(line):
                        result['reason'] = 'SEP-no-reply'
                        rejected = True
                if rejected:
                    break
                if serial is not None:
                    serial_tail = serial_tail + serial.read()
                    if args.stop_serial.encode() in serial_tail:
                        result['reason'] = 'serial-marker:' + args.stop_serial
                        break
                    serial_tail = serial_tail[-4096:]
                if milestones is not None:
                    for line in milestones.readlines():
                        fields = line.rstrip('\n').split('\t')
                        if len(fields) >= 4 and fields[2] == 'LS_SCAN_RETURN' and int(fields[3], 16) == 1:
                            completed_scans += 1
                            if completed_scans == args.scan_limit:
                                result['scan_milestone_active_seconds'] = round(float(fields[0]) - start, 6)
                    if completed_scans >= args.scan_limit:
                        result['reason'] = 'native-LS-successful-scan-limit'
                        break
                if sample is None and args.sample_after is not None and elapsed >= args.sample_after:
                    sample_log = (run / 'sample-command.log').open('w')
                    sample = subprocess.Popen(['sample', str(pid), '3', '1', '-file',
                        str(run / 'host-profile.txt')], stdout=sample_log,
                        stderr=subprocess.STDOUT)
                time.sleep(.25)
        except KeyboardInterrupt:
            result['reason'] = 'operator-paused-for-diagnosis'
        finally:
            hmp.command('stop')
            result['active_wall_seconds'] = round(time.monotonic() - start, 3)
            result['stderr_end_offset'] = stream.tell()
            result['successful_LS_scans'] = completed_scans if milestones else None
            result['host_sample_after_seconds'] = args.sample_after
            if milestones:
                milestones.close()
            if serial:
                serial.close()
            result['paused_status'] = hmp.command('info status').strip()
            (run / 'perf-cpus.txt').write_text(hmp.command('info cpus'))
            (run / 'perf-registers.txt').write_text(hmp.command('info registers'))
            if sample is not None:
                sample.wait(timeout=15)
                sample_log.close()
            (run / 'timing.json').write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
