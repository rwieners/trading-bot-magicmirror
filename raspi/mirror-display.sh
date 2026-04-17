#!/bin/bash

# Toggle the Raspberry Pi MagicMirror display on or off.
# Designed for Raspberry Pi OS Bookworm / Wayland (labwc), with fallbacks.
# Usage: ./mirror-display.sh on|off

ACTION="${1:-}"
case "$ACTION" in
  on|off) ;;
  *)
    echo "Usage: $0 on|off" >&2
    exit 1
    ;;
esac

echo "$(date '+%Y-%m-%d %H:%M:%S') — mirror-display.sh $ACTION"

STATE=0
if [ "$ACTION" = "on" ]; then
  STATE=1
fi

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"

# Preferred on Raspberry Pi OS Bookworm / labwc Wayland sessions.
if command -v /usr/bin/wlr-randr >/dev/null 2>&1 && [ -d "$XDG_RUNTIME_DIR" ]; then
  if [ -z "${WAYLAND_DISPLAY:-}" ]; then
    WAYLAND_SOCKET=$(find "$XDG_RUNTIME_DIR" -maxdepth 1 -type s -name "wayland-*" 2>/dev/null | sort | head -n1 || true)
    if [ -n "$WAYLAND_SOCKET" ]; then
      export WAYLAND_DISPLAY="${WAYLAND_SOCKET##*/}"
    fi
  fi

  if [ -z "${WAYLAND_DISPLAY:-}" ]; then
    echo "ERROR: No WAYLAND_DISPLAY found"
    exit 1
  fi

  OUTPUT="${MIRROR_OUTPUT:-$(/usr/bin/wlr-randr 2>/dev/null | /usr/bin/grep -m1 '^HDMI-A-' | /usr/bin/cut -d' ' -f1 || true)}"
  if [ -n "$OUTPUT" ]; then
    echo "Running: wlr-randr --output $OUTPUT --$ACTION"
    /usr/bin/wlr-randr --output "$OUTPUT" --"$ACTION"
    echo "OK"
    exit 0
  else
    echo "ERROR: No HDMI output found via wlr-randr"
  fi
fi

# Fallback for older Raspberry Pi display stacks.
if command -v /usr/bin/vcgencmd >/dev/null 2>&1; then
  exec /usr/bin/vcgencmd display_power "$STATE"
fi

# Last fallback for X11 sessions.
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/home/rene/.Xauthority}"
exec /usr/bin/xset dpms force "$ACTION"
