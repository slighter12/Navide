"""Real Windows API gates for the E5 Rust ConPTY handoff.

Run each gate separately with --gate w1|w2|w3 and --probe <built Rust binary>.
Output is deliberately status-only for the hosted artifact.
"""

import argparse
import ctypes
import json
import queue
import subprocess
import sys
import threading
import time
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
HANDLE = wintypes.HANDLE
DWORD = wintypes.DWORD
BOOL = wintypes.BOOL
LPDWORD = ctypes.POINTER(DWORD)


def bind(name, restype, argtypes):
    call = getattr(kernel32, name)
    call.restype = restype
    call.argtypes = argtypes
    return call


duplicate_handle = bind(
    "DuplicateHandle",
    BOOL,
    [HANDLE, HANDLE, HANDLE, ctypes.POINTER(HANDLE), DWORD, BOOL, DWORD],
)
current_process = bind("GetCurrentProcess", HANDLE, [])
close_handle = bind("CloseHandle", BOOL, [HANDLE])
get_file_type = bind("GetFileType", DWORD, [HANDLE])
get_handle_information = bind("GetHandleInformation", BOOL, [HANDLE, LPDWORD])
peek_named_pipe = bind(
    "PeekNamedPipe",
    BOOL,
    [HANDLE, ctypes.c_void_p, DWORD, LPDWORD, LPDWORD, LPDWORD],
)
# CompareObjectHandles is exported by KernelBase.dll, not kernel32.dll.
kernelbase = ctypes.WinDLL("kernelbase", use_last_error=True)
compare_object_handles = kernelbase.CompareObjectHandles
compare_object_handles.restype = BOOL
compare_object_handles.argtypes = [HANDLE, HANDLE]
read_file = bind("ReadFile", BOOL, [HANDLE, ctypes.c_void_p, DWORD, LPDWORD, ctypes.c_void_p])
write_file = bind("WriteFile", BOOL, [HANDLE, ctypes.c_void_p, DWORD, LPDWORD, ctypes.c_void_p])

FILE_TYPE_PIPE = 3
DUPLICATE_SAME_ACCESS = 2
HANDLE_FLAG_INHERIT = 1


def check(ok, label):
    if not ok:
        raise OSError(ctypes.get_last_error(), label)


def duplicate(source_process, raw):
    result = HANDLE()
    check(
        duplicate_handle(
            HANDLE(source_process),
            HANDLE(raw),
            current_process(),
            ctypes.byref(result),
            0,
            False,
            DUPLICATE_SAME_ACCESS,
        ),
        "DuplicateHandle",
    )
    return result


def peek(handle):
    available = DWORD()
    ok = peek_named_pipe(handle, None, 0, None, ctypes.byref(available), None)
    return bool(ok), available.value


def write(handle, data):
    count = DWORD()
    buffer = ctypes.create_string_buffer(data)
    check(write_file(handle, buffer, len(data), ctypes.byref(count), None), "WriteFile")
    assert count.value == len(data)


def read(handle, count):
    size = DWORD()
    buffer = ctypes.create_string_buffer(count)
    check(read_file(handle, buffer, count, ctypes.byref(size), None), "ReadFile")
    return buffer.raw[: size.value]


def gate_w1(proc, record, handles):
    handles.append(duplicate(proc._handle, record["read"]))
    assert get_file_type(handles[-1]) == FILE_TYPE_PIPE
    flags = DWORD()
    check(get_handle_information(handles[-1], ctypes.byref(flags)), "GetHandleInformation")
    assert flags.value & HANDLE_FLAG_INHERIT == 0


def gate_w2(proc, record, handles):
    read_end = duplicate(proc._handle, record["read"])
    write_end = duplicate(proc._handle, record["write"])
    second_read = duplicate(proc._handle, record["read"])
    handles.extend((read_end, write_end, second_read))
    assert get_file_type(read_end) == get_file_type(write_end) == FILE_TYPE_PIPE
    assert peek(read_end) == (True, 0)
    assert peek(write_end)[0] is False
    assert compare_object_handles(read_end, second_read)
    assert not compare_object_handles(read_end, write_end)
    marker = b"E5_W2_PIPE_MARKER"
    write(write_end, marker)
    assert peek(read_end) == (True, len(marker))
    assert read(read_end, len(marker)) == marker


def gate_w3(proc, record, handles):
    input_end = duplicate(proc._handle, record["input"])
    output_end = duplicate(proc._handle, record["output"])
    handles.extend((input_end, output_end))
    assert get_file_type(input_end) == get_file_type(output_end) == FILE_TYPE_PIPE
    assert peek(input_end)[0] is False
    assert peek(output_end)[0] is True
    assert not compare_object_handles(input_end, output_end)
    write(input_end, b"set /a 123456+654321\r\n")
    deadline = time.monotonic() + 10
    output = bytearray()
    while b"777777" not in output and time.monotonic() < deadline:
        ok, available = peek(output_end)
        assert ok, "ConPTY output pipe closed before marker"
        if available:
            output.extend(read(output_end, min(available, 4096)))
    assert b"777777" in output, "ConPTY child output was not received"
    write(input_end, b"exit\r\n")


def run(gate, probe):
    mode = "conpty" if gate == "w3" else "pipes"
    proc = subprocess.Popen(
        [probe, mode], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    handles = []
    try:
        lines = queue.Queue(maxsize=1)
        threading.Thread(target=lambda: lines.put(proc.stdout.readline()), daemon=True).start()
        try:
            line = lines.get(timeout=10)
        except queue.Empty:
            raise TimeoutError("Rust probe did not publish handles within 10 seconds") from None
        if not line:
            raise RuntimeError("Rust probe exited before publishing handles")
        record = json.loads(line)
        {"w1": gate_w1, "w2": gate_w2, "w3": gate_w3}[gate](proc, record, handles)
    finally:
        for handle in handles:
            close_handle(handle)
        if proc.poll() is None:
            proc.stdin.write(b"done\n")
            proc.stdin.flush()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=5)
            raise RuntimeError("Rust probe did not exit within 10 seconds")
    assert proc.returncode == 0, "Rust probe returned nonzero"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", choices=("w1", "w2", "w3"), required=True)
    parser.add_argument("--probe", required=True)
    args = parser.parse_args()
    build = sys.getwindowsversion().build
    try:
        run(args.gate, args.probe)
    except Exception as error:  # noqa: BLE001 - one status-only outcome for every gate failure
        print(
            f"runtime=navide-cli-runtime platform=windows build={build} "
            f"case=G-{args.gate.upper()} status=failed stage={type(error).__name__}",
            flush=True,
        )
        raise SystemExit(1) from None
    print(
        f"runtime=navide-cli-runtime platform=windows build={build} "
        f"case=G-{args.gate.upper()} status=passed stage=real-win32",
        flush=True,
    )


if __name__ == "__main__":
    main()
