"""Windows stand-in for the POSIX `bash -ilc <command>` wrapper of the shell-runtime case.

Like bash with a foreground child, it runs the command as a child process, waits for it, passes its exit code on
and does not end on the Ctrl+C that is meant for the child (the console delivers CTRL_C_EVENT to every process on
it). `pwsh -Command` is not used for this: it ends on Ctrl+C itself, which a shell that waits for a foreground
program does not.
"""
import signal
import subprocess
import sys

signal.signal(signal.SIGINT, lambda *_: None)
raise SystemExit(subprocess.call(sys.argv[1:]))
