"""The ticket-04 isolation guard's registration of the E3 sidecar launch (wire v1): exact form, checked control descriptor.

Pure functions of `process_env_guard`: `match_runtime_launch` (the argv) and `control_fd_problem` (the descriptor). The
guard itself (installed in a fixture process) is exercised by the other isolation_guard tests, which launch the real sidecar
through this form.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys

import pytest

from tests.cli_regression.support.process_env_guard import control_fd_problem, match_runtime_launch, runtime_launch

RUNTIME = '/fixture/navide-cli-runtime'
GENERATION = 'ab' * 16
DATA = Path('/fixture/data')
PID = 4242
JOURNAL = DATA / 'runtime-ownership' / f'{GENERATION}.jsonl'


def good(fd: int = 9) -> list[str]:
    return runtime_launch(RUNTIME, GENERATION, PID, JOURNAL, fd)


def test_the_exact_form_is_matched_and_yields_the_control_descriptor():
    assert good(9) == [RUNTIME, '--wire', '1', '--runtime-generation', GENERATION, '--owner-pid', '4242',
                       '--ownership-journal', str(JOURNAL), '--control-fd', '9']
    assert match_runtime_launch(good(9), RUNTIME, PID, DATA) == 9
    assert match_runtime_launch(tuple(good(12)), RUNTIME, PID, DATA) == 12


@pytest.mark.parametrize('label,mutate', [
    ('the old --protocol form', lambda a: [a[0], '--protocol', '1', *a[3:7]]),
    ('a different wire version', lambda a: [*a[:2], '2', *a[3:]]),
    ('another executable', lambda a: ['/elsewhere/runtime', *a[1:]]),
    ('a non-hex generation', lambda a: [*a[:4], 'z' * 32, *a[5:]]),
    ('a short generation', lambda a: [*a[:4], 'ab' * 8, *a[5:]]),
    ('another owner pid', lambda a: [*a[:6], '1', *a[7:]]),
    ('a journal of another generation', lambda a: [*a[:8], str(DATA / 'runtime-ownership' / ('cd' * 16 + '.jsonl')), *a[9:]]),
    ('a journal outside the data dir', lambda a: [*a[:8], '/tmp/journal.jsonl', *a[9:]]),
    ('a missing control fd', lambda a: a[:9]),
    ('a trailing extra flag', lambda a: [*a, '--fail-startup']),
    ('a descriptor below 3', lambda a: [*a[:10], '2']),
    ('a non-decimal descriptor', lambda a: [*a[:10], '9a']),
    ('a descriptor with a sign', lambda a: [*a[:10], '+9']),
    ('a descriptor with leading zeros', lambda a: [*a[:10], '09']),
    ('a non-ascii digit', lambda a: [*a[:10], '\u0669']),
    ('swapped flags', lambda a: [a[0], '--runtime-generation', a[4], '--wire', '1', *a[5:]]),
])
def test_every_deviation_is_refused_with_no_blanket_allowance(label, mutate):
    assert match_runtime_launch(mutate(good(9)), RUNTIME, PID, DATA) is None, label


def test_a_non_sequence_argv_is_refused():
    assert match_runtime_launch('navide', RUNTIME, PID, DATA) is None
    assert match_runtime_launch(None, RUNTIME, PID, DATA) is None


@pytest.mark.skipif(sys.platform == 'win32', reason='AF_UNIX socketpair')
def test_the_control_descriptor_must_be_the_only_passed_open_unix_stream_socket():
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    other = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    pipe_r, pipe_w = os.pipe()
    try:
        fd = child.fileno()
        assert control_fd_problem(fd, (fd,)) is None
        assert control_fd_problem(fd, [fd]) is None
        assert 'exactly' in control_fd_problem(fd, ())
        assert 'exactly' in control_fd_problem(fd, None)
        assert 'exactly' in control_fd_problem(fd, (fd, parent.fileno()))
        assert 'exactly' in control_fd_problem(fd, (parent.fileno(),))
        assert 'not a socket' in control_fd_problem(pipe_r, (pipe_r,))
        assert 'AF_UNIX stream' in control_fd_problem(other[0].fileno(), (other[0].fileno(),))
        closed = os.dup(fd)
        os.close(closed)
        assert 'not an open descriptor' in control_fd_problem(closed, (closed,))
    finally:
        for end in (parent, child, *other):
            end.close()
        os.close(pipe_r)
        os.close(pipe_w)
