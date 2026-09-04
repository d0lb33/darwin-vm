#!/usr/bin/env python3
"""Extract line-numbered TRACE_JSON records from an LLDB probe log."""
import json
import os
import sys


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: sm_dynamic_report.py LLDB_LOG [OUTPUT.json]")
    log_path = os.path.abspath(sys.argv[1])
    output_path = (os.path.abspath(sys.argv[2]) if len(sys.argv) == 3 else
                   os.path.splitext(log_path)[0] + ".trace.json")
    records = []
    with open(log_path, errors="replace") as stream:
        for line_number, line in enumerate(stream, 1):
            marker = "TRACE_JSON "
            offset = line.find(marker)
            if offset < 0:
                continue
            record = json.loads(line[offset + len(marker):])
            record["artifact"] = log_path
            record["artifact_line"] = line_number
            records.append(record)
    report = {
        "schema": "darwin-vm.sm-dynamic-trace.v1",
        "lldb_log": log_path,
        "record_count": len(records),
        "records": records,
    }
    with open(output_path, "w") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print("wrote %s (%d records)" % (output_path, len(records)))


if __name__ == "__main__":
    main()
