//! E1 wire v1 frame codec (design/e1-runtime-protocol-v7.md, sections 2 and 2.3).
//!
//! Pure functions only: no I/O, no clock, no global state. Every frame, in both directions:
//! a fixed 32-byte big-endian header, a UTF-8 JSON object `meta` and a raw `blob`.
use serde_json::Value;

pub const HEADER_LEN: usize = 32;
pub const WIRE_VERSION: u8 = 1;
pub const FLAG_HAS_FD: u16 = 1;

/// Python -> sidecar limits (section 2.1).
pub const MAX_META_P2R: u32 = 4 * 1024 * 1024;
pub const MAX_BLOB_P2R: u32 = 1024 * 1024;
/// Sidecar -> Python: `meta_len + blob_len` (section 2.1, MUST).
pub const MAX_FRAME_R2P: u32 = 65_536;
/// A `HAS_FD` frame (only `SESSION_OWNED`) stays this small (section 2.1).
pub const MAX_HAS_FD_FRAME: usize = 4096;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum FrameType {
    Request = 1,
    Response = 2,
    Error = 3,
    Event = 4,
}
impl FrameType {
    fn from_u8(value: u8) -> Option<Self> {
        Some(match value {
            1 => Self::Request,
            2 => Self::Response,
            3 => Self::Error,
            4 => Self::Event,
            _ => return None,
        })
    }
}

/// Message codes (section 2.3).
pub mod code {
    pub const CREATE: u16 = 0x0001;
    pub const ADOPT: u16 = 0x0002;
    pub const RESIZE: u16 = 0x0003;
    pub const KILL: u16 = 0x0004;
    pub const CANCEL: u16 = 0x0005;
    pub const PING: u16 = 0x0006;
    pub const SHUTDOWN: u16 = 0x0007;
    pub const OBSERVE: u16 = 0x0008;
    pub const TTY_EOF: u16 = 0x0009;
    /// Windows only: a protocol error on POSIX (named so the table is complete).
    #[allow(dead_code)]
    pub const TTY_SERVICING: u16 = 0x000A;
    pub const SESSION_OWNED: u16 = 0x0101;
    pub const SESSION_HANGUP: u16 = 0x0102;
    pub const SESSION_EXITED: u16 = 0x0103;
    pub const SESSION_CLEANED: u16 = 0x0104;
    pub const SESSION_OWNERSHIP: u16 = 0x0105;
    pub const OBSERVATION_PART: u16 = 0x0106;
    pub const SESSION_ROOT_LIVE: u16 = 0x0107;
    pub const FATAL: u16 = 0x01FF;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Direction {
    /// A frame the sidecar receives.
    PythonToRust,
    /// A frame the sidecar sends.
    RustToPython,
}

/// What a malformed frame violated (one variant per rule of section 2.1/2.3).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Violation {
    Magic,
    Version,
    Flags,
    FrameType,
    Code,
    Direction,
    Meta,
    RuntimeTag,
    Correlation,
    Limit,
}
impl Violation {
    pub fn name(self) -> &'static str {
        match self {
            Self::Magic => "magic",
            Self::Version => "version",
            Self::Flags => "flags",
            Self::FrameType => "frame_type",
            Self::Code => "code",
            Self::Direction => "direction",
            Self::Meta => "meta",
            Self::RuntimeTag => "runtime_tag",
            Self::Correlation => "correlation",
            Self::Limit => "limit",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Header {
    pub frame_type: FrameType,
    pub code: u16,
    pub flags: u16,
    pub correlation_id: u64,
    pub runtime_tag: u64,
    pub meta_len: u32,
    pub blob_len: u32,
}
impl Header {
    pub fn body_len(&self) -> usize {
        self.meta_len as usize + self.blob_len as usize
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Frame {
    pub frame_type: FrameType,
    pub code: u16,
    pub flags: u16,
    pub correlation_id: u64,
    pub runtime_tag: u64,
    pub meta: Value,
    pub blob: Vec<u8>,
}

/// The first 8 bytes of the 128-bit runtime generation token (32 lowercase hex characters),
/// big-endian (section 2.4).
pub fn runtime_tag(generation: &str) -> Option<u64> {
    if generation.len() != 32 || !generation.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    u64::from_str_radix(&generation[..16], 16).ok()
}

/// Is (type, code) a member of the wire-v1 table, valid in this direction on this platform?
fn code_valid(frame_type: FrameType, code: u16, direction: Direction) -> Result<(), Violation> {
    // TTY_SERVICING exists on Windows only; on POSIX it stays a protocol error.
    let request_code = (code::CREATE..=code::TTY_EOF).contains(&code)
        || (cfg!(windows) && code == code::TTY_SERVICING);
    match (direction, frame_type) {
        (Direction::PythonToRust, FrameType::Request)
        | (Direction::RustToPython, FrameType::Response | FrameType::Error) => {
            if request_code {
                Ok(())
            } else {
                Err(Violation::Code)
            }
        }
        (Direction::RustToPython, FrameType::Event) => {
            if matches!(
                code,
                code::SESSION_OWNED
                    | code::SESSION_HANGUP
                    | code::SESSION_EXITED
                    | code::SESSION_CLEANED
                    | code::SESSION_OWNERSHIP
                    | code::OBSERVATION_PART
                    | code::SESSION_ROOT_LIVE
                    | code::FATAL
            ) {
                Ok(())
            } else {
                Err(Violation::Code)
            }
        }
        _ => Err(Violation::Direction),
    }
}

/// Validate a 32-byte header received in `direction`, before any body byte is read.
pub fn parse_header(
    bytes: &[u8; HEADER_LEN],
    direction: Direction,
    expected_tag: u64,
) -> Result<Header, Violation> {
    if bytes[0] != b'N' || bytes[1] != b'C' {
        return Err(Violation::Magic);
    }
    if bytes[2] != WIRE_VERSION {
        return Err(Violation::Version);
    }
    let frame_type = FrameType::from_u8(bytes[3]).ok_or(Violation::FrameType)?;
    let code = u16::from_be_bytes([bytes[4], bytes[5]]);
    let flags = u16::from_be_bytes([bytes[6], bytes[7]]);
    let correlation_id = u64::from_be_bytes(bytes[8..16].try_into().unwrap());
    let runtime_tag = u64::from_be_bytes(bytes[16..24].try_into().unwrap());
    let meta_len = u32::from_be_bytes(bytes[24..28].try_into().unwrap());
    let blob_len = u32::from_be_bytes(bytes[28..32].try_into().unwrap());
    code_valid(frame_type, code, direction)?;
    if flags & !FLAG_HAS_FD != 0 {
        return Err(Violation::Flags);
    }
    if flags & FLAG_HAS_FD != 0
        && !(direction == Direction::RustToPython
            && frame_type == FrameType::Event
            && code == code::SESSION_OWNED)
    {
        return Err(Violation::Flags);
    }
    if runtime_tag != expected_tag {
        return Err(Violation::RuntimeTag);
    }
    if direction == Direction::PythonToRust
        && frame_type == FrameType::Request
        && correlation_id == 0
    {
        return Err(Violation::Correlation);
    }
    match direction {
        Direction::PythonToRust => {
            if meta_len > MAX_META_P2R || blob_len > MAX_BLOB_P2R {
                return Err(Violation::Limit);
            }
        }
        Direction::RustToPython => {
            if u64::from(meta_len) + u64::from(blob_len) > u64::from(MAX_FRAME_R2P) {
                return Err(Violation::Limit);
            }
        }
    }
    Ok(Header {
        frame_type,
        code,
        flags,
        correlation_id,
        runtime_tag,
        meta_len,
        blob_len,
    })
}

/// Decode a complete body (`meta` then `blob`) for a validated header.
pub fn decode_body(header: Header, body: &[u8]) -> Result<Frame, Violation> {
    if body.len() != header.body_len() {
        return Err(Violation::Limit);
    }
    let (meta_bytes, blob) = body.split_at(header.meta_len as usize);
    let meta = if meta_bytes.is_empty() {
        Value::Object(Default::default())
    } else {
        let text = std::str::from_utf8(meta_bytes).map_err(|_| Violation::Meta)?;
        let value: Value = serde_json::from_str(text).map_err(|_| Violation::Meta)?;
        if !value.is_object() {
            return Err(Violation::Meta);
        }
        value
    };
    Ok(Frame {
        frame_type: header.frame_type,
        code: header.code,
        flags: header.flags,
        correlation_id: header.correlation_id,
        runtime_tag: header.runtime_tag,
        meta,
        blob: blob.to_vec(),
    })
}

/// Encode from an already serialised `meta` (the golden-vector entry point).
pub fn encode_raw(
    frame_type: FrameType,
    code: u16,
    flags: u16,
    correlation_id: u64,
    runtime_tag: u64,
    meta: &[u8],
    blob: &[u8],
) -> Vec<u8> {
    let mut out = Vec::with_capacity(HEADER_LEN + meta.len() + blob.len());
    out.extend_from_slice(b"NC");
    out.push(WIRE_VERSION);
    out.push(frame_type as u8);
    out.extend_from_slice(&code.to_be_bytes());
    out.extend_from_slice(&flags.to_be_bytes());
    out.extend_from_slice(&correlation_id.to_be_bytes());
    out.extend_from_slice(&runtime_tag.to_be_bytes());
    out.extend_from_slice(&(meta.len() as u32).to_be_bytes());
    out.extend_from_slice(&(blob.len() as u32).to_be_bytes());
    out.extend_from_slice(meta);
    out.extend_from_slice(blob);
    out
}

pub fn encode(frame: &Frame) -> Vec<u8> {
    let meta = serde_json::to_vec(&frame.meta).expect("a JSON value is serialisable");
    encode_raw(
        frame.frame_type,
        frame.code,
        frame.flags,
        frame.correlation_id,
        frame.runtime_tag,
        &meta,
        &frame.blob,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const VECTORS: &str = include_str!("../tests/vectors/frame-vectors.json");

    fn unhex(text: &str) -> Vec<u8> {
        (0..text.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
            .collect()
    }
    fn direction(name: &str) -> Direction {
        match name {
            "p2r" => Direction::PythonToRust,
            "r2p" => Direction::RustToPython,
            other => panic!("direction {other}"),
        }
    }

    /// F1: every golden vector is reproduced byte for byte by this encoder and decodes back to
    /// its fields (the same file is read by the Python codec).
    #[test]
    fn golden_vectors_encode_byte_for_byte_and_decode() {
        let file: Value = serde_json::from_str(VECTORS).unwrap();
        let tag = u64::from_str_radix(file["runtime_tag"].as_str().unwrap(), 16).unwrap();
        for vector in file["valid"].as_array().unwrap() {
            let name = vector["name"].as_str().unwrap();
            let expected = unhex(vector["hex"].as_str().unwrap());
            let frame_type =
                FrameType::from_u8(vector["frame_type"].as_u64().unwrap() as u8).unwrap();
            let encoded = encode_raw(
                frame_type,
                vector["code"].as_u64().unwrap() as u16,
                vector["flags"].as_u64().unwrap() as u16,
                vector["correlation_id"].as_u64().unwrap(),
                tag,
                vector["meta"].as_str().unwrap().as_bytes(),
                &unhex(vector["blob"].as_str().unwrap()),
            );
            assert_eq!(encoded, expected, "{name}");
            let header = parse_header(
                expected[..HEADER_LEN].try_into().unwrap(),
                direction(vector["direction"].as_str().unwrap()),
                tag,
            )
            .unwrap_or_else(|v| panic!("{name}: {v:?}"));
            let frame = decode_body(header, &expected[HEADER_LEN..]).unwrap();
            assert_eq!(
                frame.correlation_id,
                vector["correlation_id"].as_u64().unwrap()
            );
            assert_eq!(
                frame.blob,
                unhex(vector["blob"].as_str().unwrap()),
                "{name}"
            );
            // `encode` (serde_json meta) round-trips to an equal frame.
            let again = decode_body(
                parse_header(
                    encode(&frame)[..HEADER_LEN].try_into().unwrap(),
                    direction(vector["direction"].as_str().unwrap()),
                    tag,
                )
                .unwrap(),
                &encode(&frame)[HEADER_LEN..],
            )
            .unwrap();
            assert_eq!(again, frame, "{name}");
        }
    }

    /// The worked example of section 2.6, spelled out independently of the vector file.
    #[test]
    fn section_2_6_worked_example() {
        let bytes = encode_raw(
            FrameType::Request,
            code::PING,
            0,
            1,
            0x0102030405060708,
            b"{}",
            b"",
        );
        assert_eq!(
            bytes,
            [
                0x4E, 0x43, 0x01, 0x01, 0x00, 0x06, 0x00, 0x00, 0, 0, 0, 0, 0, 0, 0, 1, 1, 2, 3, 4,
                5, 6, 7, 8, 0, 0, 0, 2, 0, 0, 0, 0, 0x7B, 0x7D
            ]
        );
    }

    /// F2/F3 (sidecar side): every invalid vector is rejected with the declared rule.
    #[test]
    fn invalid_vectors_are_rejected_by_the_declared_rule() {
        let file: Value = serde_json::from_str(VECTORS).unwrap();
        let tag = u64::from_str_radix(file["runtime_tag"].as_str().unwrap(), 16).unwrap();
        for vector in file["invalid"].as_array().unwrap() {
            let name = vector["name"].as_str().unwrap();
            if cfg!(windows) && name == "TTY_SERVICING on POSIX" {
                continue;
            }
            let bytes = unhex(vector["hex"].as_str().unwrap());
            let dir = direction(vector["direction"].as_str().unwrap());
            let header: [u8; HEADER_LEN] = bytes[..HEADER_LEN].try_into().unwrap();
            // A header that passes must come with its whole body in the vector: a header-only
            // vector has to be refused by the header rules themselves, before any body byte.
            let outcome = parse_header(&header, dir, tag).and_then(|h| {
                assert!(
                    bytes.len() >= HEADER_LEN + h.body_len(),
                    "{name}: header accepted without its body"
                );
                decode_body(h, &bytes[HEADER_LEN..HEADER_LEN + h.body_len()]).map(|_| h)
            });
            let why = vector["why"].as_str().unwrap();
            match outcome {
                Err(violation) => assert_eq!(violation.name(), why, "{name}"),
                Ok(_) => panic!("{name}: accepted"),
            }
        }
    }

    #[test]
    fn every_split_point_reassembles_to_the_same_frame() {
        let meta = serde_json::to_vec(&json!({"a":[1,2,3],"b":"é"})).unwrap();
        let bytes = encode_raw(
            FrameType::Event,
            code::SESSION_EXITED,
            0,
            0,
            7,
            &meta,
            &[9, 8, 7],
        );
        for cut in 0..=bytes.len() {
            let mut joined = bytes[..cut].to_vec();
            joined.extend_from_slice(&bytes[cut..]);
            let header = parse_header(
                joined[..HEADER_LEN].try_into().unwrap(),
                Direction::RustToPython,
                7,
            )
            .unwrap();
            assert_eq!(header.body_len(), meta.len() + 3);
            assert_eq!(
                decode_body(header, &joined[HEADER_LEN..]).unwrap().blob,
                [9, 8, 7]
            );
        }
    }

    #[test]
    fn runtime_tag_is_the_first_eight_token_bytes_big_endian() {
        assert_eq!(
            runtime_tag("0102030405060708090a0b0c0d0e0f10"),
            Some(0x0102030405060708)
        );
        assert_eq!(runtime_tag("short"), None);
        assert_eq!(runtime_tag("zz02030405060708090a0b0c0d0e0f10"), None);
    }
}
