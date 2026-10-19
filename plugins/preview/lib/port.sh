#!/usr/bin/env bash
set -euo pipefail

preview_free_port() {
  python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

preview_port_in_use() {
  local host="${1:-127.0.0.1}"
  local port="$2"
  python3 - "$host" "$port" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket()
s.settimeout(0.5)
try:
    s.connect((host, port))
except OSError:
    sys.exit(1)
finally:
    s.close()
sys.exit(0)
PY
}

# Wait until host:port accepts connections.
# Optional 3rd arg: app_pid — if that process exits before the port opens, return 2.
preview_wait_port() {
  local host="${1:-127.0.0.1}"
  local port="$2"
  local app_pid="${3:-}"
  local i
  for i in $(seq 1 60); do
    if [[ -n "$app_pid" ]] && ! kill -0 "$app_pid" 2>/dev/null; then
      return 2
    fi
    if preview_port_in_use "$host" "$port"; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}
