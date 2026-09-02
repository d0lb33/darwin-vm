#!/bin/bash
set -euo pipefail

# darwin-vm launcher
#
# Display (new):
#   DISPLAY_MODE  cocoa | sdl | vnc | none     default: cocoa (macOS) / sdl (elsewhere)
#   FB            WxH[@scale] or "off"         default: iPhone 828x1792@2, Mac 1440x900@1
#   FBMODE        text | graphics              default: text (verbose console on screen)
#   VNC           vnc display spec             default: :0  (used when DISPLAY_MODE=vnc)
#   BOOT_ARGS     override the XNU boot-args entirely
#
# With a display, XNU is booted with serial=2 (serial *input*, console *output*
# on the screen), so the console shows up in the window and keys typed into the
# window (or into this terminal) go to the guest. Use DISPLAY_MODE=none (or
# ./run.sh --nographic) for the classic serial-only root shell.

FIRMWARE_DIR="firmware"
QEMU="qemu-sptm/build/qemu-system-aarch64"

usage() {
    cat <<USAGE
usage: $0 [--nographic] [--vnc [:N]] [--sdl] [--cocoa] [--fb WxH[@scale]|off] [--graphics]

  --nographic      serial console in this terminal only (classic mode)
  --vnc [:N]       expose the screen over VNC on display :N (default :0)
  --sdl / --cocoa  pick the QEMU display backend
  --fb SPEC        framebuffer size, eg. 828x1792@2 (iPhone) or 1440x900 (Mac)
  --graphics       boot graphics (progress spinner) instead of the text console

Environment variables DISPLAY_MODE, FB, FBMODE, VNC, BOOT_ARGS do the same thing.
USAGE
    exit 1
}

default_display_mode() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo cocoa
    else
        echo sdl
    fi
}

default_fb() {
    local devname=""
    if [[ -f "${FIRMWARE_DIR}/info" ]]; then
        devname="$(head -n1 "${FIRMWARE_DIR}/info")"
    fi
    case "${devname}" in
        Mac*)   echo "1440x900@1" ;;
        *)      echo "828x1792@2" ;;
    esac
}

fix_tty() {
    stty sane 2>/dev/null || true
}

boot_qemu() {
    local display_mode="${DISPLAY_MODE:-$(default_display_mode)}"
    local fb="${FB:-$(default_fb)}"
    local fbmode="${FBMODE:-text}"
    local vnc="${VNC:-:0}"

    if [[ "${display_mode}" == "none" ]]; then
        fb="off"
    fi

    # serial=3: serial in+out (console on the UART)
    # serial=2: serial in only, console output goes to the framebuffer
    local serial_mode=3
    if [[ "${fb}" != "off" ]]; then
        serial_mode=2
    fi

    local boot_args="${BOOT_ARGS:-rd=md0 serial=${serial_mode} -v -noprogress wdt=-1 wlan-olyhal-abort}"

    args=(
        -M darwin
        -bootkc   "${FIRMWARE_DIR}/bootkc"
        -dtree    "${FIRMWARE_DIR}/dtree"
        -tc       "${FIRMWARE_DIR}/ramdisk.tc"
        -ramdisk  "${FIRMWARE_DIR}/ramdisk.dmg"
        -args     "${boot_args}"
        -serial mon:stdio
        -m 8G
    )

    if [[ -f "${FIRMWARE_DIR}/sptm" ]]; then
        args+=(
            -sptm     "${FIRMWARE_DIR}/sptm"
            -txm      "${FIRMWARE_DIR}/txm"
        )
    fi

    if [[ "${fb}" != "off" ]]; then
        args+=( -fb "${fb}" -fbmode "${fbmode}" )
    fi

    case "${display_mode}" in
        none)  args+=( -display none ) ;;
        vnc)   args+=( -display none -vnc "${vnc}" ); echo "VNC server on ${vnc} (eg. open vnc://localhost:590${vnc#:})" ;;
        cocoa) args+=( -display cocoa,zoom-to-fit=on ) ;;
        sdl)   args+=( -display sdl ) ;;
        *)     args+=( -display "${display_mode}" ) ;;
    esac

    "${QEMU}" "${args[@]}"
}

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --nographic) DISPLAY_MODE=none ;;
            --vnc)       DISPLAY_MODE=vnc; if [[ "${2:-}" == :* ]]; then VNC="$2"; shift; fi ;;
            --sdl)       DISPLAY_MODE=sdl ;;
            --cocoa)     DISPLAY_MODE=cocoa ;;
            --fb)        FB="${2:?--fb needs WxH[@scale]}"; shift ;;
            --graphics)  FBMODE=graphics ;;
            -h|--help)   usage ;;
            *)           echo "unknown option: $1"; usage ;;
        esac
        shift
    done
    export DISPLAY_MODE FB FBMODE VNC BOOT_ARGS 2>/dev/null || true

    trap 'fix_tty' EXIT
    boot_qemu
}

main "$@"
