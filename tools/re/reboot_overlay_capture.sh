#!/bin/bash
set -u
NS=$HOME/dvm-artifacts/native-smc; DISK="$1"; O="$2"; mkdir -p "$O"; rm -f "$O"/*.sock
touch "$O/relay.events"
export DARWIN_DCP_EPIC=all DARWIN_DCP_IOMFB=4 DARWIN_DCP_IOMFB_CB="D120::4,D586:9b040000fc090000:4" \
  DARWIN_DCP_IOMFB_COMPLETE=1 \
  DARWIN_DCP_IOMFB_OUT="A401=01,A000=01,A454=01000000,A033=4152474200000000000000000000000000000000000000000000000000000000000000000000000001000000,A453=9b040000fc090000,A412=01000000" \
  DARWIN_DCP_IOMFB_RPC_TRACE=0 DARWIN_DCP_IOMFB_SCANOUT=1 DARWIN_DCP_REPLY=1 DARWIN_PAUTH_CACHE=on DARWIN_PMU_DEBUG=0 DARWIN_RTC_PV=0
"$NS/qemu-system-aarch64" -M darwin -bootkc "$NS/bootkc" -dtree "$NS/system.dtree" \
  -tc "$HOME/dvm-artifacts/research/cellular-bootstrap-20260907/stage2/system.tc" \
  -ramdisk "$NS/ramdisk" -args "rootdev=disk1s1 ignition_level=1 launchd_unsecure_cache=1 serial=3 -v wdt=-1 wlan-olyhal-abort" \
  -m 12G -sptm "$NS/sptm" -txm "$NS/txm" -smp 6 -accel tcg,thread=multi \
  -fb 1179x2556 -fbmode graphics -display none \
  -drive if=none,id=ans,file="$DISK",format=qcow2 \
  -chardev socket,id=uart0,path="$O/uart.sock",server=on,wait=off -serial chardev:uart0 \
  -monitor unix:"$O/monitor.sock",server=on,wait=off > "$O/stderr.log" 2>&1 &
echo "QPID=$!"
