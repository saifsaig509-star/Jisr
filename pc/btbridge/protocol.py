"""Wire protocol shared by the phone app and the PC companion.

Frame types (the payload of every frame):

  T_JSON  (1)  UTF-8 JSON control message
  T_AUDIO (2)  raw PCM 16-bit little-endian, PC -> phone
  T_SPP   (3)  1 byte channel id + raw serial bytes, both directions
  T_FILE  (4)  4 bytes big-endian file id + one chunk of that file, both directions
               (the file_* JSON messages around it are described in files.py)

The handshake (challenge / hello) travels as plain frames:  1 byte type | 4 bytes big-endian
length | payload.  Right after it every frame is encrypted - see secure.py for that format.
"""
import json
import struct

VERSION = 2
PORT = 47810            # TCP (phone <-> PC) and UDP (discovery)
LOCAL_API_PORT = 47812  # TCP, 127.0.0.1 only, JSON lines
SPP_BASE_PORT = 47900   # TCP, 127.0.0.1 only, one port per serial channel
DISCOVERY_MAGIC = b"BTBRIDGE?"

T_JSON, T_AUDIO, T_SPP, T_FILE = 1, 2, 3, 4
HEADER = struct.Struct(">BI")
MAX_FRAME = 1 << 20


async def read_frame(reader):
    hdr = await reader.readexactly(HEADER.size)
    ftype, length = HEADER.unpack(hdr)
    if length > MAX_FRAME:
        raise ValueError("frame too large")
    data = await reader.readexactly(length) if length else b""
    return ftype, data


def pack(ftype: int, payload: bytes) -> bytes:
    return HEADER.pack(ftype, len(payload)) + payload


def json_bytes(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def pack_json(obj) -> bytes:
    """Plain (handshake-time) JSON frame."""
    return pack(T_JSON, json_bytes(obj))

