# Offline investigation of the 647.621 ms Home transition gap

Source: CA_DEV_HOME1, exact iOS 27 24A5430a. No VM boot, driver change or
new GPU execution was used for this investigation. Analysis output:
`/Users/jdolbe1/dvm-artifacts/research/gpu-home-gap-20260907/analysis.json`.

## Findings

Transition thumbnails30→31 show icons moving into the Home grid. Their actual
framebuffer-delivery interval is 647.621 ms. D594 acknowledgement follows each
endpoint in 0.780 and 0.834 ms; thumbnail capture costs 0.283 and 0.255 ms.

The inferred corresponding host interval is RPC1085 completion → RPC1225
completion, 646.5735 ms. It includes:

| Work | Count |
| --- | ---: |
| Texture upload chunks | 88 |
| Texture allocations | 11 |
| Buffer allocations | 8 |
| Resource process metadata | 20 |
| Releases | 9 |
| Render-buffer writes | 3 |
| Render submission | 1 |

All 140 RPCs succeed. Their host-service durations total 23.116334 ms. The
final GPU batch itself takes 0.697500 ms (included in service, not additive).
Recorded inter-request gaps total 623.347376 ms. The remaining ~0.110 ms in
the host interval is timestamp/record-boundary overhead. Largest individual
inter-request gaps: 62.233, 54.055 and 42.745 ms, all before texture chunks.
The first RPC arrives 19.436 ms after the previous batch completion: this
is not a single 648 ms pause before any submission activity.

The eight uploaded textures are handles259,261,263,265,267,269,271,273. Each
is 204×204 RGBA16F, shared storage, sampled usage1, 332,928 bytes. Eleven
chunks per image transfer 2,663,424 bytes total (2.54 MiB). Inspection of their
captured pixels identifies News, App Store, Maps, FaceTime, Phone, Safari,
Messages and Music icons. These are actual captured guest bytes, not invented
test content. Allocation descriptors and SHA-256-verified chunk contents are
retained with the analysis.

## Evidence limits and static explanation

The frame/RPC association is an **inference**, supported by complete ordered
streams: 493 GPU batches and 493 completed presentations. Include the one
`renderStageCommit`, not just the 492 `renderSubmit` calls, or the association
is off by one. The selected intervals differ by only 1.0475 ms. Across all
492 intervals the signed discrepancy has median -0.013417 ms, p95 2.033542 ms,
range -33.699 to 28.38975 ms. A shared per-frame submission ID would provide
stronger association. Absolute host/QEMU timestamps differ by about ten seconds;
the analysis compares durations within each clock and does not subtract those
absolute clocks to claim latency.

`DVMUploadTextureChunks` in `driver_guest.m` copies a subrange, base64 encodes
it, performs a synchronous RPC and checks its acknowledgement before advancing.
`driver_mmio_transport.inc` then serializes JSON, calculates CRC, copies it into
shared RAM, rings the doorbell and polls completion. The raw underlying
transport is shared RAM, but these texture uploads still use repeated framed
JSON transactions.

`driver_mmio_peer.py` starts its service timer after notification reception,
CRC verification and JSON parsing. After recording completion it writes each
upload JSON, rereads it for hashing and appends its journal. The run controller
also reads logs, checks display/input state and waits for events between pumps.
Therefore **623 ms is not measured pure guest CPU time or pure transport time**:
it includes guest encoding/scheduling, notification/host scheduling, pre-service
parsing and post-service capture/controller costs. It cannot be attributed more
precisely from this capture. Nor is this a full-scene replay.

## Smallest next experiment after reload tooling is ready

1. Repeat the same Home transition with icons already resident, without driver
   replacement between the cold/warm pair. Record whether these uploads recur.
2. Match timing by sequence: guest encoding, doorbell, completion observation;
   host notification receipt, decode, reply publication and evidence-write end.
   Use durations within clock domains. Compare full payload capture with a
   bounded metadata-only timing run; preserve correctness and failure checks.
3. If repeated upload transaction overhead dominates, send raw texture bytes
   through a bounded owned shared staging region with fewer notifications.
   Preserve validation, transaction identity, acknowledgement, safe reuse and
   the existing chunk fallback. Do not change shader code to address this gap.

The leading hypothesis is cold icon upload/submission overhead. The measured
host service, GPU work, capture thumbnails and D594 completion are too small
to account for the full hitch individually. Cold icon creation/rasterization
and TCG scheduling remain possible contributors, not proven causes.

Reproduce:

```sh
python3 tools/gpu/analyze_transition_gap.py \
  /Users/jdolbe1/dvm-artifacts/research/gpu-development-profile-20260907/evidence/CA_DEV_HOME1 \
  --after-thumbnail 30 --out <new-analysis-directory>
```
