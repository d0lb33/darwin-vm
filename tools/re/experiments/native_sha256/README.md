# Native SHA-256 helper experiment (not enabled)

Applied to QEMU 7c223ce, this uses host ARM SHA2 intrinsics for four guest
SHA256 helpers, with scalar fallback on other compiler targets.

Two million scalar/native differential helper calls passed (aliases and vector
tails, ASan/UBSan), but the exact iOS boot took 111.3 seconds against the
98.278–100.180-second control range. No performance win is established.

Apply `qemu.patch` in an isolated checkout, then run
`python3 tools/re/test_native_sha256.py /absolute/artifact/output-directory`
from its parent project before building. The test extracts actual original
HEAD and current helper bodies; it requires an ARM64 SHA2-capable compiler
and host. Do not enable this based on the arithmetic checks alone.
