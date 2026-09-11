#!/bin/bash
# Boot a fresh un-personalized native-smc disk paused with a gdbstub, resolve the
# live shared-cache slide, attach lldb with seed_default_wallpaper.py (Buddy
# completion + WallpaperKit default-wallpaper restore in one SpringBoard hijack),
# then leave the guest running with a UART so tools/input/relay.py can wake the
# display for visual verification. Persistent overlay is kept.
set -u
REPO=/Users/jdolbe1/Downloads/darwin-vm-metal-driver
NS=$HOME/dvm-artifacts/native-smc
O=${O:-/tmp/dvm/SEED1/run}
PORT=${PORT:-1236}
CACHE_GLOB="$HOME/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e*"
mkdir -p "$O"
rm -f "$O"/*.sock
if [[ ! -e "$O/disk.qcow2" ]]; then
  qemu-img create -f qcow2 -F qcow2 -b "$NS/cellular-plan-20260907/system.qcow2" "$O/disk.qcow2" >/dev/null || exit 1
fi
touch "$O/relay.events"
export DARWIN_DCP_EPIC=all DARWIN_DCP_IOMFB=4 DARWIN_DCP_IOMFB_CB="D120::4,D586:9b040000fc090000:4" \
  DARWIN_DCP_IOMFB_COMPLETE=1 \
  DARWIN_DCP_IOMFB_OUT="A401=01,A000=01,A454=01000000,A033=4152474200000000000000000000000000000000000000000000000000000000000000000000000001000000,A453=9b040000fc090000,A412=01000000" \
  DARWIN_DCP_IOMFB_RPC_TRACE=0 DARWIN_DCP_IOMFB_SCANOUT=1 DARWIN_DCP_REPLY=1 DARWIN_PAUTH_CACHE=on \
  DARWIN_PMU_DEBUG=0 DARWIN_RTC_PV=0
# Match SETUP6's proven native-input wiring: one UART (serial=3, console+helper
# share it) that tools/input/relay.py drives; plus a gdbstub for the seed.
"$NS/qemu-system-aarch64" -M darwin -bootkc "$NS/bootkc" -dtree "$NS/system.dtree" \
  -tc "$HOME/dvm-artifacts/research/cellular-bootstrap-20260907/stage2/system.tc" \
  -ramdisk "$NS/ramdisk" -args "rootdev=disk1s1 ignition_level=1 launchd_unsecure_cache=1 serial=3 -v wdt=-1 wlan-olyhal-abort" \
  -m 12G -sptm "$NS/sptm" -txm "$NS/txm" -smp 6 -accel tcg,thread=multi \
  -fb 1179x2556 -fbmode graphics -display none \
  -drive if=none,id=ans,file="$O/disk.qcow2",format=qcow2 \
  -chardev socket,id=uart0,path="$O/uart.sock",server=on,wait=off -serial chardev:uart0 \
  -monitor unix:"$O/monitor.sock",server=on,wait=off \
  -S -gdb tcp::$PORT > "$O/stderr.log" 2>&1 &
QPID=$!
echo "qemu pid $QPID (gdb :$PORT); waiting for monitor socket"
t=0; until [[ -S "$O/monitor.sock" ]]; do sleep 0.3; t=$((t+1)); [[ $t -gt 120 ]] && { echo "no monitor"; exit 1; }; done
echo "resolving slide..."
python3 "$REPO/tools/re/resolve_live_dsc.py" "$O/monitor.sock" "$CACHE_GLOB" --cont > "$O/slide.json" 2>"$O/slide.err"
SLIDE=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['slide'])" "$O/slide.json" 2>/dev/null)
if [[ -z "$SLIDE" ]]; then echo "slide FAILED"; cat "$O/slide.err" "$O/slide.json"; kill $QPID; exit 1; fi
echo "slide=$SLIDE"
cat > "$O/lldb.cmd" <<CMD
settings set target.process.stop-on-sharedlibrary-events false
gdb-remote $PORT
command script import $REPO/tools/re/seed_wallpaper_bg.py
script seed_wallpaper_bg.install(lldb.debugger, $SLIDE)
continue
CMD
echo "attaching lldb"
script -q /dev/null lldb -b -s "$O/lldb.cmd" > "$O/lldb.log" 2>&1 &
LLDB=$!
echo "LLDB pid $LLDB; waiting for SEED_WP_DONE (up to ~8 min)"
for i in $(seq 1 120); do
  sleep 4
  if grep -q SEED_WP_DONE "$O/lldb.log" 2>/dev/null; then echo "SEED DONE at ${i}x4s"; break; fi
  if ! kill -0 $QPID 2>/dev/null; then echo "qemu died"; break; fi
done
grep -aE "SEED_WP" "$O/lldb.log" 2>/dev/null | tail -30
echo "--- guest left running: pid $QPID, monitor $O/monitor.sock, uart $O/uart.sock ---"
echo "QPID=$QPID LLDB=$LLDB"
