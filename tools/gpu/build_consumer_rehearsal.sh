#!/bin/bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:?new output directory}
test ! -e "$out"
mkdir -p "$out"
flags=(-fobjc-arc -fobjc-arc-exceptions -O1 -Wall -Wextra -Werror -Wno-protocol -Wno-objc-protocol-property-synthesis -Wno-deprecated-declarations)
xcrun clang "${flags[@]}" -DDVM_CA_PROBE -DDVM_CA_REHEARSAL "$repo/tools/gpu/driver_guest.m" "$repo/tools/gpu/driver_workload.m" "$repo/tools/gpu/driver_client.m" -framework Metal -framework Foundation -framework IOSurface -framework QuartzCore -framework CoreGraphics -o "$out/driver_client"
xcrun clang "${flags[@]}" "$repo/tools/gpu/driver_host.m" -framework Metal -framework Foundation -o "$out/driver_host"
cp "$repo/tools/gpu/consumer_"* "$repo/tools/gpu/driver_guest.m" "$repo/tools/gpu/driver_host.m" "$repo/tools/gpu/driver_client.m" "$out/"
