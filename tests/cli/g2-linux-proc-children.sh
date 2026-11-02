#!/usr/bin/env bash
# G2 t14: say in the log whether /proc/<pid>/task/<tid>/children exists, and fail when it does not.
# The Linux sidecar's scoped ownership mode reads that file (kernel CONFIG_PROC_CHILDREN; native/navide-cli-runtime/src/linux.rs
# `RealStartup::children_readable`). Without it the sidecar falls back to the full /proc scan and prints "scoped ownership scan is off",
# which no hosted step greps for, so a run could be measured in the wrong mode with nothing naming the cause. This is the direct check.
# The sidecar reads its own pid's file; for a process's main thread pid and tid are equal, so this shell's $$ stands in for it.
set -euo pipefail

file="${G2_PROC_CHILDREN_FILE_FOR_TEST:-/proc/$$/task/$$/children}"
kernel="$(uname -r)"

if [ ! -r "$file" ]; then
  echo "proc children: MISSING or unreadable: $file (kernel $kernel); the Linux scoped ownership mode needs CONFIG_PROC_CHILDREN" >&2
  exit 1
fi
cat "$file" > /dev/null
echo "proc children: present: $file (kernel $kernel); CONFIG_PROC_CHILDREN is available"
