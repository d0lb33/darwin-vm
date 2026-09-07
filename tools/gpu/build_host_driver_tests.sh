#!/bin/bash
# Focused native tests; does not rebuild or stage any guest boot artifacts.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:?new output directory}
test ! -e "$out"
mkdir -p "$out"
xcrun clang -O1 -Wall -Wextra -Werror -fsanitize=address,undefined "$repo/tools/gpu/test_dirty_buffer_range.c" -o "$out/test_dirty_buffer_range"
xcrun clang -O1 -Wall -Wextra -Werror -fsanitize=address,undefined "$repo/tools/gpu/test_metal_library_slice.c" -o "$out/test_metal_library_slice"
flags=(-fobjc-arc -fobjc-arc-exceptions -O1 -Wall -Wextra -Werror -Wno-deprecated-declarations -Wno-protocol -Wno-objc-protocol-property-synthesis)
xcrun clang "${flags[@]}" "$repo/tools/gpu/driver_host.m" -framework Metal -framework Foundation -o "$out/driver_host"
for name in driver_contract_test driver_capability_test test_render_writeback test_owned_surface_frontend test_framebuffer_read test_private_block_texture test_submission_queues test_blit_forwarding test_library_loading test_texture_1d; do
    xcrun clang "${flags[@]}" "$repo/tools/gpu/$name.m" "$repo/tools/gpu/driver_guest.m" -framework Metal -framework Foundation -framework IOSurface -o "$out/$name"
done
xcrun clang "${flags[@]}" "$repo/tools/gpu/test_surface_handoff.m" -framework Foundation -framework IOSurface -o "$out/test_surface_handoff"
xcrun clang "${flags[@]}" "$repo/tools/gpu/driver_client.m" "$repo/tools/gpu/driver_workload.m" "$repo/tools/gpu/driver_guest.m" -framework Metal -framework Foundation -framework IOSurface -o "$out/driver_client"
cp "$repo/tools/gpu/"*.m "$repo/tools/gpu/"*.h "$repo/tools/gpu/"*.inc "$out/"

xcrun clang "${flags[@]}" "$repo/tools/gpu/test_native_mip_alias.m" -framework Metal -framework Foundation -o "$out/test_native_mip_alias"
