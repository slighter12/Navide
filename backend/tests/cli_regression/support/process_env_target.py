"""Inert, created synthetic target; never queries another process."""
import signal
import sys

print('SYNTHETIC_ENV_TARGET_READY', flush=True)
while True:
    signal.pause()
