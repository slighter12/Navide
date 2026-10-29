"""The registry of exact fixture byte strings (E2 v10 section 3 item 4d), stdlib only so the fixture peer (a separate process on the base interpreter) can import it.

Moved verbatim from the draft `e2-v10-tools.py` (`FIXTURE_TOKENS`, `TOKEN_SEPARATOR`, `INPUTS`, `WORKLOAD`); the canonical hash of these four objects is the frozen fixture-registry digest.
"""
from __future__ import annotations

FIXTURE_TOKENS = {
    'start':      'BASELINE_LEDGER_START:{nonce}',          # nonce: 32 lowercase hex characters (128 bits); whitespace-free (located in the log too)
    'win_begin':  'BASELINE_WIN:{wid}:BEGIN',               # wid: i<peer> (io window) or b<peer>r<kk> (burst round); whitespace-free
    'win_end':    'BASELINE_WIN:{wid}:END',
    'ack':        'BASELINE_ACK {text}',                    # renderer-stream only; text = '<round>:<input>' (io) or 'during-burst-r<kk>' (mid-burst probe)
    'burst_line': 'BASELINE_BURST r{kk} {line:04d} {xs}',   # renderer-stream only; xs = 'x' * burstWidth
    'done':       'BASELINE_DONE:r{kk}',                    # THE burst-round end marker and THE burst latency endpoint (exact bytes 'BASELINE_DONE:r<kk>\n')
    'final':      'BASELINE_FINAL:{accepted}:{digest}',     # accepted: decimal, at most 10 digits; digest: 64 lowercase hex
}
# token family -> exact separator after the name (anything else is a non-conforming spelling)
TOKEN_SEPARATOR = {'BASELINE_LEDGER_START': ':', 'BASELINE_WIN': ':', 'BASELINE_DONE': ':', 'BASELINE_FINAL': ':', 'BASELINE_ACK': ' ', 'BASELINE_BURST': ' '}
INPUTS = {'io': '{round}:{input}', 'burst_start': 'burst r{kk}', 'mid_burst': 'during-burst-r{kk}'}   # client -> peer input texts
WORKLOAD = {'ioRounds': 1500, 'burstLines': 128, 'burstWidth': 2048, 'burstRounds': {'1': 40, '4': 10}, 'input': 'café 世界 \\ quote\' " ; | &'}

def tok(name: str, **kw) -> str:
    return FIXTURE_TOKENS[name].format(**kw)


def line(name: str, **kw) -> bytes:
    return (tok(name, **kw) + '\n').encode('utf-8')
