#!/usr/bin/env python3
"""Read-only extraction for GPU_CHANNEL_AUX14..16 and exact-guest UART COLD3.

Inputs are retained archives; outputs require a new, explicitly named directory.
"""
import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description='read-only GPU evidence extraction')
parser.add_argument('--output', type=Path, required=True,
                    help='new output directory; created exclusively for a replay')
args = parser.parse_args()
OUT = args.output.resolve()
OUT.mkdir()  # exclusive: a replay must never merge with another collection
AUX_ROOT = Path.home()/'dvm-artifacts/research/gpu-transport-ios27-20260905'
FINAL_BYTE_CHECK = AUX_ROOT / 'GPU_CHANNEL_FINAL_BYTE_CHECK.json'
ROUND_ROOT = Path.home()/'dvm-artifacts/research/gpu-roundtrip-ios27-20260905'
TRIALS = ('GPU_CHANNEL_AUX14', 'GPU_CHANNEL_AUX15', 'GPU_CHANNEL_AUX16')
UART = ROUND_ROOT / 'GPU_ROUNDTRIP_COLD3'
DOC = Path(__file__).resolve().parents[2]/'docs/re/gpu-roundtrip-ios27.md'
GPU_SOURCE = ROUND_ROOT / 'gpu'

def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def json_load(path):
    with path.open() as source:
        return json.load(source)

def json_lines(path):
    with path.open() as source:
        return [json.loads(line) for line in source if line.strip()]

def source_record(path, location, kind):
    return {'path': str(path), 'sha256': sha256(path), 'location': location, 'kind': kind}

def event_locations(serial, needle):
    with serial.open(errors='replace') as source:
        return [line_no for line_no, line in enumerate(source, 1) if needle in line]

def parse_fields(line):
    return dict(re.findall(r'([A-Za-z_]+)=([^\s]+)', line))

def raw_check(path):
    size = path.stat().st_size
    seed = bytes((i * 37 + (i >> 8) * 11 + 19) & 255 for i in range(1048576))
    with path.open('rb') as source:
        source.seek(0x100000)
        got_seed = source.read(1048576)
        source.seek(0x400000)
        got_output = source.read(1048576)
    expected_output = bytes(value ^ 0x5a for value in seed)
    return {
        'path': str(path), 'file_bytes': size, 'sha256_whole_retained_file': sha256(path),
        'seed_offset_hex': '0x100000', 'output_offset_hex': '0x400000', 'bytes_checked': 1048576,
        'seed_formula': '(i*37+(i>>8)*11+19)&255', 'output_formula': 'seed XOR 0x5a',
        'seed_matches': got_seed == seed, 'output_matches': got_output == expected_output,
        'seed_mismatch_count': sum(a != b for a, b in zip(got_seed, seed)),
        'output_mismatch_count': sum(a != b for a, b in zip(got_output, expected_output)),
    }

aux_rows = []
host_rows = []
evidence = {'method': {'read_only': True, 'collector': str(Path(__file__))},
            'inputs': [], 'auxiliary_trials': {}, 'uart': {}, 'validation_failures': []}
final_byte_check = json_load(FINAL_BYTE_CHECK)
evidence['inputs'].append(source_record(FINAL_BYTE_CHECK, '$[0..2]', 'archive_final_byte_check'))
expected_final_check = {trial: {'host_seed_bytes_verified': 1048576,
                                'guest_output_bytes_verified': 1048576,
                                'host_requests_verified': 10} for trial in TRIALS}
actual_final_check = {entry.get('tag'): {key: entry.get(key) for key in expected_final_check[TRIALS[0]]}
                      for entry in final_byte_check if entry.get('tag') in expected_final_check}
if actual_final_check != expected_final_check:
    evidence['validation_failures'].append({'contract': 'archive_final_byte_check_exact_records',
                                            'expected': expected_final_check, 'actual': actual_final_check})
evidence['archive_final_byte_check'] = actual_final_check

for trial in TRIALS:
    directory = AUX_ROOT / trial
    result_path, host_path, serial_path, raw_path = (directory/'result.json', directory/'aux-host.jsonl',
                                                      directory/'serial.log', directory/'aux.raw')
    result, host = json_load(result_path), json_lines(host_path)
    evidence['inputs'].extend([
        source_record(result_path, '$.events[] and top-level run fields', 'result_json'),
        source_record(host_path, 'JSON-lines 1..10', 'host_peer_events'),
        source_record(serial_path, 'serial lines selected by exact marker', 'guest_serial'),
        source_record(raw_path, 'whole file plus offsets 0x100000 and 0x400000', 'raw_namespace'),
    ])
    event_index = {}
    def record_event(key, index, event):
        if key in event_index:
            evidence['validation_failures'].append({'trial': trial, 'contract': 'unique_guest_marker',
                                                    'marker': key, 'first_event_index': event_index[key][0],
                                                    'duplicate_event_index': index})
        else:
            event_index[key] = (index, event)
    for index, event in enumerate(result.get('events', [])):
        line = event.get('line', '')
        if 'GPU_LOAD_AUX_READ' in line: record_event('read', index, event)
        elif 'GPU_LOAD_AUX_WRITE' in line: record_event('write', index, event)
        elif 'GPU_LOAD_AUX_PING' in line:
            match = re.search(r'\bseq=(\d+)', line)
            if match: record_event('seq' + match.group(1), index, event)
            else: evidence['validation_failures'].append({'trial': trial, 'contract': 'ping_has_sequence', 'event_index': index})
        elif 'GPU_LOAD_AUX_RESULT' in line: record_event('aux_result', index, event)
        elif 'GPU_LOAD_COMPLETE result=pass scope=auxiliary-byte-transport' in line: record_event('byte_completion', index, event)
        elif 'DVM_INPUT_READY ' in line: record_event('input_ready', index, event)
        elif 'DVM_INPUT_ACK 900002 1' in line: record_event('ack900002', index, event)
    for required in ['read', 'write', 'aux_result', 'byte_completion'] + [f'seq{i}' for i in range(1, 11)]:
        if required not in event_index:
            evidence['validation_failures'].append({'trial': trial, 'contract': 'required_guest_marker_present', 'marker': required})
    for key, call in [('read', 'read_1MiB'), ('write', 'write_1MiB')]:
        index, event = event_index.get(key, (None, {}))
        fields = parse_fields(event.get('line', ''))
        if index is None:
            evidence['validation_failures'].append({'trial': trial, 'contract': 'bulk_marker_parseable', 'call': key})
        aux_rows.append({
            'trial': trial, 'group': 'bulk', 'call': call, 'sequence': '',
            'guest_event_seconds_runner_clock': event.get('seconds', ''),
            'guest_call_elapsed_seconds': fields.get('seconds', ''), 'bytes': fields.get('bytes', ''),
            'verified_or_valid': fields.get('verified', ''), 'polls': '', 'expected_timeout': '',
            'classification': 'completed_bulk', 'nominal_timeout_budget_seconds': '',
            'elapsed_minus_nominal_seconds': '', 'result_json_event_index': index if index is not None else '',
            'serial_log_lines': ','.join(map(str, event_locations(serial_path, 'GPU_LOAD_AUX_' + key.upper())))
        })
    for sequence in range(1, 11):
        index, event = event_index.get('seq' + str(sequence), (None, {}))
        fields = parse_fields(event.get('line', ''))
        elapsed = float(fields['seconds']) if 'seconds' in fields else None
        klass = 'normal_sequence_1_to_8' if sequence <= 8 else 'deliberate_timeout_9' if sequence == 9 else 'recovery_10'
        expected_valid = '0' if sequence == 9 else '1'
        expected_timeout = '1' if sequence == 9 else '0'
        if index is None or fields.get('valid') != expected_valid or fields.get('expected_timeout') != expected_timeout:
            evidence['validation_failures'].append({'trial': trial, 'contract': 'guest_ping_flags', 'sequence': sequence,
                                                    'expected_valid': expected_valid, 'actual_valid': fields.get('valid'),
                                                    'expected_timeout': expected_timeout, 'actual_timeout': fields.get('expected_timeout')})
        aux_rows.append({
            'trial': trial, 'group': 'ping', 'call': 'ping', 'sequence': sequence,
            'guest_event_seconds_runner_clock': event.get('seconds', ''), 'guest_call_elapsed_seconds': fields.get('seconds', ''),
            'bytes': '', 'verified_or_valid': fields.get('valid', ''), 'polls': fields.get('polls', ''),
            'expected_timeout': fields.get('expected_timeout', ''), 'classification': klass,
            'nominal_timeout_budget_seconds': '1.0' if sequence == 9 else '',
            'elapsed_minus_nominal_seconds': (elapsed - 1.0) if sequence == 9 and elapsed is not None else '',
            'result_json_event_index': index if index is not None else '',
            'serial_log_lines': ','.join(map(str, event_locations(serial_path, f'GPU_LOAD_AUX_PING seq={sequence} ')))
        })
    seen_host_sequences = set()
    if len(host) != 10:
        evidence['validation_failures'].append({'trial': trial, 'contract': 'ten_host_peer_events', 'actual': len(host)})
    prior = None
    for line_no, record in enumerate(host, 1):
        event_time = record['seconds']
        sequence = record.get('sequence')
        if sequence in seen_host_sequences:
            evidence['validation_failures'].append({'trial': trial, 'contract': 'unique_host_sequence', 'sequence': sequence, 'line': line_no})
        seen_host_sequences.add(sequence)
        expected_response_written = sequence != 9
        expected_timeout_injected = sequence == 9
        if record.get('request_verified') is not True or record.get('response_written') != expected_response_written or record.get('injected_timeout') != expected_timeout_injected:
            evidence['validation_failures'].append({'trial': trial, 'contract': 'host_peer_flags', 'sequence': sequence,
                                                    'actual': record})
        host_rows.append({
            'trial': trial, 'sequence': record['sequence'],
            'host_peer_event_seconds_since_aux_peer_monotonic_origin': event_time,
            'interval_since_previous_host_peer_event_seconds': '' if prior is None else event_time - prior,
            'request_verified': record['request_verified'], 'response_written': record['response_written'],
            'injected_timeout': record['injected_timeout'],
            'clock_origin': 'AuxProbe.started = time.monotonic() at aux_probe.py:30; logged seconds=time.monotonic()-self.started at aux_probe.py:49 after request validation and after response write (except withheld seq9)',
            'source_aux_host_jsonl_line': line_no,
        })
        prior = event_time
    if seen_host_sequences != set(range(1, 11)):
        evidence['validation_failures'].append({'trial': trial, 'contract': 'host_sequences_exactly_1_through_10',
                                                'actual': sorted(seen_host_sequences)})
    raw = raw_check(raw_path)
    if not raw['seed_matches'] or not raw['output_matches']:
        evidence['validation_failures'].append({'trial': trial, 'contract': 'raw_seed_or_output_pattern', 'detail': raw})
    complete = event_index.get('byte_completion', (None, {}))[1]
    ready = event_index.get('input_ready', (None, {}))[1]
    ack = event_index.get('ack900002', (None, {}))[1]
    ready_send = result.get('input_ready_sync_sent_seconds')
    early_send = result.get('input_sync_sent_seconds')
    timing = {
        'runner_clock_origin': 'started = time.monotonic() at run_guest_load.py:109; result event seconds recorded from time.monotonic()-started at lines 154-156',
        'early_input_sync_send_seconds': early_send,
        'byte_completion_event_seconds': complete.get('seconds'),
        'input_READY_event_seconds': ready.get('seconds'),
        'fresh_ready_sync_send_seconds': ready_send,
        'ACK900002_event_seconds': ack.get('seconds'),
        'ACK900002_latency_seconds_same_runner_clock': (ack.get('seconds') - ready_send) if ack and ready_send is not None else None,
        'byte_completion_to_input_READY_seconds_same_runner_clock': (ready.get('seconds') - complete.get('seconds')) if ready and complete else None,
    }
    outcome = {
        'overall_result_passed_field': result.get('passed'), 'stop_reason': result.get('stop_reason'),
        'auxiliary_field_present': 'auxiliary' in result, 'raw_validation': raw, 'timing': timing,
        'byte_subset_observed': bool(complete), 'input_sync_ack_observed_field': result.get('input_sync_ack_observed'),
        'event_locations': {key: {'result_json_event_index': index, 'serial_log_lines': event_locations(serial_path, event['line'])}
                            for key, (index, event) in event_index.items()},
    }
    if trial == 'GPU_CHANNEL_AUX15':
        stop_path = directory/'input-check-stop.json'
        outcome['interrupted_input_gate'] = json_load(stop_path)
        evidence['inputs'].append(source_record(stop_path, '$', 'input_gate_stop_record'))
    evidence['auxiliary_trials'][trial] = outcome

uart_result_path, uart_worker_path, uart_bridge_path = UART/'result.json', UART/'host-worker.stderr', UART/'bridge.jsonl'
uart_result = json_load(uart_result_path)
uart_roundtrip = uart_result['roundtrip']
uart_guest_report = uart_roundtrip['guest_report']
worker_lines = uart_worker_path.read_text().splitlines()
runs = [(line_number, line) for line_number, line in enumerate(worker_lines, 1) if 'DIAG event=run ' in line]
if len(runs) != 9 or len(uart_guest_report.get('runs', [])) != 9:
    evidence['validation_failures'].append({'contract': 'cited_UART_has_exactly_nine_operations',
                                            'host_worker_run_lines': len(runs), 'result_runs': len(uart_guest_report.get('runs', []))})
uart_rows = []
recreated_lines, reused_lines = [], []
for ordinal, (guest, (source_line, line)) in enumerate(zip(uart_guest_report.get('runs', []), runs), 1):
    fields = parse_fields(line)
    for key in ('wall_ms', 'gpu_ms', 'delay_ms'):
        fields[key] = re.search(rf'\b{key}=([^\s]+)', line).group(1)
    texture_state = re.search(r'\btextures=([^\s]+)', line).group(1)
    (recreated_lines if texture_state == 'recreated' else reused_lines).append(source_line)
    uart_rows.append({
        'operation_ordinal': ordinal, 'host_worker_id': fields.get('id', ''),
        'guest_generation': guest.get('generation', ''), 'host_generation': fields.get('generation', ''),
        'guest_pass': guest.get('pass', ''), 'guest_elapsed_ms': guest.get('elapsed_ms', ''),
        'guest_commit_ms': guest.get('commit_ms', ''), 'guest_status': guest.get('status', ''),
        'guest_early_read_rejected': guest.get('early_read_rejected', ''),
        'guest_direct_surface_equal': guest.get('direct_surface_equal', ''),
        'host_status': re.search(r'\bstatus=([^\s]+)', line).group(1), 'host_wall_ms': fields['wall_ms'],
        'host_gpu_ms': fields['gpu_ms'], 'host_delay_ms': fields['delay_ms'],
        'textures': texture_state,
        'pre_dispatch_differs': re.search(r'\bpre_dispatch_differs=([^\s]+)', line).group(1),
        'host_worker_stderr_line': source_line,
    })
raw_missing = [name for name in ('guest-requests.bin', 'host-responses.bin', 'wire.log') if not (UART/name).exists()]
evidence['inputs'].extend([
    source_record(uart_result_path, '$.roundtrip.guest_report.runs[0..8]', 'cited_uart_result_json'),
    source_record(uart_worker_path, 'lines ' + ','.join(map(str, [number for number, _ in runs])) + ' (nine DIAG event=run)', 'cited_uart_host_worker'),
    source_record(uart_bridge_path, 'JSON-lines 1..3', 'cited_uart_bridge'),
    source_record(UART/'guest-requests.bin', 'full raw retained request capture', 'cited_uart_raw_request_capture'),
    source_record(UART/'host-responses.bin', 'full raw retained response capture', 'cited_uart_raw_response_capture'),
    source_record(UART/'independent-verification.json', '$ (nine-operation byte verifier record)', 'cited_uart_independent_verification'),
    source_record(GPU_SOURCE/'guest_work.m', 'lines 124..134 use NSDate.timeIntervalSinceReferenceDate for guest timings', 'static_clock_provenance'),
    source_record(GPU_SOURCE/'metal_proxy_server.m', 'lines 314..371 use NSDate.timeIntervalSinceReferenceDate for host wall_ms', 'static_clock_provenance'),
    source_record(DOC, 'lines 180..206 cite GPU_ROUNDTRIP_COLD3; locator context only', 'documentation_locator'),
])
evidence['uart'] = {
    'selected_cited_run': str(UART), 'result_passed': uart_result.get('passed'),
    'host_only': uart_guest_report.get('host_only'), 'test': 'COLD3 actual guest/host roundtrip',
    'operation_count_result': len(uart_guest_report.get('runs', [])), 'operation_count_host_worker': len(runs),
    'raw_payload_files_missing_in_selected_cited_uart_directory': raw_missing,
    'generation_recreation_evidence_source': str(uart_worker_path) + ': recreated lines ' + ','.join(map(str, recreated_lines)) + '; reused lines ' + ','.join(map(str, reused_lines)),
    'clock_semantics': 'Guest elapsed_ms and host wall_ms are independently derived from NSDate.timeIntervalSinceReferenceDate (guest_work.m:124,134; metal_proxy_server.m:314,369), not CLOCK_MONOTONIC.',
    'do_not_compute': 'No cross-clock subtraction; do not call guest elapsed minus host GPU time transport time.',
}
evidence['unavailable_measurements'] = [
    {'measurement': 'host request-arrival timestamp (H0)', 'reason': 'aux-host.jsonl seconds is emitted after validation and response write/withholding, not at request arrival.'},
    {'measurement': 'host response-write completion timestamp (H4)', 'reason': 'not independently logged; only the post-action peer event record exists.'},
    {'measurement': 'H0-to-H4 service time or precise peer processing latency', 'reason': 'H0 and H4 are not retained.'},
    {'measurement': 'guest/runner event seconds minus aux-peer event seconds', 'reason': 'AuxProbe.started and run_guest_load.py started are separate monotonic origins with no retained offset.'},
    {'measurement': 'guest elapsed_ms minus host wall_ms/gpu_ms as transport time', 'reason': 'prior GPU values use separate NSDate timing domains; the record does not establish a common clock.'},
]

def write_csv(path, rows):
    fields = list(rows[0]) if rows else []
    with path.open('x', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)

write_csv(OUT/'auxiliary_calls.csv', aux_rows)
write_csv(OUT/'host_events.csv', host_rows)
write_csv(OUT/'uart_operations.csv', uart_rows)
with (OUT/'evidence.json').open('x') as target:
    json.dump(evidence, target, indent=2, sort_keys=True); target.write('\n')
print(json.dumps({'output': str(OUT), 'auxiliary_calls': len(aux_rows),
                  'host_events': len(host_rows), 'uart_operations': len(uart_rows),
                  'validation_failures': len(evidence['validation_failures'])}))
sys.exit(bool(evidence['validation_failures']))
