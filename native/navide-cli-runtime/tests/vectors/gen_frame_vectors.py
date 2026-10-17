import json, struct, sys
TAG = bytes.fromhex("0102030405060708")
def frame(ftype, code, flags, cid, tag, meta: bytes, blob: bytes, magic=b"NC", ver=1):
    return (magic + bytes([ver, ftype]) + struct.pack(">HHQ", code, flags, cid) + tag
            + struct.pack(">II", len(meta), len(blob)) + meta + blob)
NAMES = {1:"CREATE",2:"ADOPT",3:"RESIZE",4:"KILL",5:"CANCEL",6:"PING",7:"SHUTDOWN",8:"OBSERVE",9:"TTY_EOF"}
EV = {0x101:"SESSION_OWNED",0x102:"SESSION_HANGUP",0x103:"SESSION_EXITED",0x104:"SESSION_CLEANED",
      0x105:"SESSION_OWNERSHIP",0x106:"OBSERVATION_PART",0x107:"SESSION_ROOT_LIVE",0x1FF:"FATAL"}
valid, invalid = [], []
def add(name, ftype, code, flags, cid, meta: str, blob: bytes, direction):
    b = frame(ftype, code, flags, cid, TAG, meta.encode(), blob)
    valid.append(dict(name=name, direction=direction, frame_type=ftype, code=code, flags=flags,
        correlation_id=cid, runtime_tag=TAG.hex(), meta=meta, blob=blob.hex(), hex=b.hex()))
cid = 1
add("PING example (spec 2.6)", 1, 6, 0, 1, "{}", b"", "p2r")
for c, n in NAMES.items():
    cid += 1
    blob = bytes(range(0, 8)) if c == 8 else b""
    add(f"REQUEST {n}", 1, c, 0, cid, '{"k":%d}' % c, blob, "p2r")
for c, n in NAMES.items():
    cid += 1
    add(f"RESPONSE {n}", 2, c, 0, cid, '{"ok":true}', bytes([0,255]) if c == 8 else b"", "r2p")
    cid += 1
    add(f"ERROR {n}", 3, c, 0, cid, '{"code":"BAD_REQUEST","stage":"x","message":"m","effect":"none"}', b"", "r2p")
for c, n in EV.items():
    cid += 1
    flags = 1 if c == 0x101 else 0
    add(f"EVENT {n}", 4, c, flags, 0 if c != 0x106 else cid, '{"e":%d}' % c, bytes([1,2,3]) if c == 0x106 else b"", "r2p")
add("zero-length meta is valid", 1, 6, 0, 99, "", b"", "p2r")
def bad(name, direction, b, why): invalid.append(dict(name=name, direction=direction, hex=b.hex(), why=why))
ok = frame(1, 6, 0, 5, TAG, b"{}", b"")
bad("bad magic", "p2r", frame(1,6,0,5,TAG,b"{}",b"",magic=b"XX"), "magic")
bad("bad version", "p2r", frame(1,6,0,5,TAG,b"{}",b"",ver=2), "version")
bad("reserved flag bit 1", "p2r", frame(1,6,2,5,TAG,b"{}",b""), "flags")
bad("HAS_FD on request", "p2r", frame(1,6,1,5,TAG,b"{}",b""), "flags")
bad("frame_type 0", "p2r", frame(0,6,0,5,TAG,b"{}",b""), "frame_type")
bad("frame_type 5", "p2r", frame(5,6,0,5,TAG,b"{}",b""), "frame_type")
bad("code outside table (0x0042)", "p2r", frame(1,0x42,0,5,TAG,b"{}",b""), "code")
bad("code 0", "p2r", frame(1,0,0,5,TAG,b"{}",b""), "code")
bad("TTY_SERVICING on POSIX", "p2r", frame(1,0x0A,0,5,TAG,b"{}",b""), "code")
bad("event sent to the sidecar", "p2r", frame(4,0x101,0,0,TAG,b"{}",b""), "direction")
bad("response sent to the sidecar", "p2r", frame(2,6,0,5,TAG,b"{}",b""), "direction")
bad("request sent to python", "r2p", frame(1,6,0,5,TAG,b"{}",b""), "direction")
bad("event code in a request", "p2r", frame(1,0x103,0,5,TAG,b"{}",b""), "code")
bad("request code in an event", "r2p", frame(4,6,0,0,TAG,b"{}",b""), "code")
bad("HAS_FD on a non-SESSION_OWNED event", "r2p", frame(4,0x103,1,0,TAG,b"{}",b""), "flags")
bad("response with HAS_FD", "r2p", frame(2,6,1,5,TAG,b"{}",b""), "flags")
bad("invalid UTF-8 meta", "p2r", frame(1,6,0,5,TAG,b"\xff\xfe",b""), "meta")
bad("meta not an object", "p2r", frame(1,6,0,5,TAG,b"[1]",b""), "meta")
bad("meta not JSON", "p2r", frame(1,6,0,5,TAG,b"{x",b""), "meta")
bad("wrong runtime_tag", "p2r", frame(1,6,0,5,bytes(8),b"{}",b""), "runtime_tag")
bad("zero correlation id request", "p2r", frame(1,6,0,0,TAG,b"{}",b""), "correlation")
h2 = frame(2,6,0,5,TAG,b"{}",b"")[:24] + struct.pack(">II", 65537, 0)
bad("sidecar frame over max_frame_r2p (header only, rejected before the body)", "r2p", h2, "limit")
h3 = frame(2,6,0,5,TAG,b"{}",b"")[:24] + struct.pack(">II", 60000, 5537)
bad("meta_len + blob_len over max_frame_r2p (header only)", "r2p", h3, "limit")
hdr = frame(1,6,0,5,TAG,b"{}",b"")[:32]
hdr = hdr[:24] + struct.pack(">II", 4194305, 0)
bad("meta_len over max_meta_p2r (header only)", "p2r", hdr, "limit")
hdr = hdr[:24] + struct.pack(">II", 2, 1048577)
bad("blob_len over max_blob_p2r (header only)", "p2r", hdr, "limit")
json.dump(dict(runtime_tag=TAG.hex(), valid=valid, invalid=invalid), open("frame-vectors.json","w"), indent=1)
print(len(valid), len(invalid))
