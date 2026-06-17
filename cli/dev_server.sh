#!/usr/bin/env bash
# Back-compat shim — the launcher is now the `robot` CLI.
#   cli/robot start          (foreground, like this script)
#   cli/robot start --bg     (detached)
#   cli/robot logs           (tail the latest log)
#   cli/robot status | stop | restart | url | help
exec "$(dirname "$0")/robot" start "$@"
