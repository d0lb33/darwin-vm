# Paired host UIKit control, 2026-09-07

Host rehearsal only. `CA_UIKIT_GLASS_PAIRED6` through `9` render the same prepared
CALayer tree serially through native Metal and the forwarding driver, using the
host QuartzCore AIR and the supported profile. All 12 frame pairs are byte
identical, including changing geometry/opacity and both renderer orders.
The forwarded path records actual render passes and draws. These results are
not an independent pixel oracle for the exact guest or system compositor proof.

The harness attaches the tree to only the active CARenderer. Attempt 5 attached
it to both and submitted zero forwarded draws; that result was rejected.
Earlier failures include a backend capability query affected by native test
overrides, missing scene activation, and an explicit UISceneErrorDomain error
when requesting activation without multiple-scene support. The successful
harness validates the backend before applying native test overrides and
starts rendering from scene activation. Only one connected scene is accepted.

Reproduction (a new output directory each time):

```sh
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer \
python3 tools/gpu/run_uikit_host.py /tmp/dvm/CA_UIKIT_GLASS_PAIRED_NEW \
  --effect glass --window --frames 3 --animate --paired-native first \
  --forwarded-library /Users/jdolbe1/dvm-artifacts/research/gpu-uikit-host-air-20260907/QuartzCore-host-air.metallib
```

Use `--paired-native last` for the reverse order. `--reuse-build` checks source
dependencies, compiler arguments and the prior binary hash before reuse.
Prepared scenes differ between app launches; compare each frame to its paired
reference, rather than claiming that independent native launches are identical.
Evidence, including failures 1–5, manifests, raw pixels and logs:
`/Users/jdolbe1/dvm-artifacts/research/gpu-uikit-paired-20260907/index.json`.

Further host-control refinement is deferred in favor of boot discovery and
actual iOS compositor integration at the user's direction.
