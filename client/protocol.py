"""Wire format used inside the peer-to-peer TLS streams.

Every frame starts with a 13-byte header ``!BQI`` = (type, arg, length):

    JSON  frame: type=1, arg=0,           length=len(json)      then UTF-8 JSON
    CHUNK frame: type=2, arg=chunk index, length=payload bytes  then 32-byte SHA-256 + payload

64-bit ``arg`` makes chunk indices (and therefore offsets = index * chunk_size) safe far
beyond 1 TB.

Streams (each one TLS connection, unidirectional after the handshake so that an OpenSSL
connection object is never used by two threads at once):

    idx 0  ctl_s2r   control, sender -> receiver (manifest, ping, finish, cancel)
    idx 1  ctl_r2s   control, receiver -> sender (accept, ack, nack, pong, complete)
    idx 2+ data      chunks, sender -> receiver
"""
from __future__ import annotations

import struct

PROTO_VERSION = 1
HDR = struct.Struct("!BQI")
T_JSON = 1
T_CHUNK = 2
DIGEST_LEN = 32
MAX_JSON = 16 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
MIN_CHUNK = 64 * 1024

KIND_CTL_S2R = "ctl_s2r"
KIND_CTL_R2S = "ctl_r2s"
KIND_DATA = "data"
KIND_PROBE = "probe"
KINDS = (KIND_CTL_S2R, KIND_CTL_R2S, KIND_DATA, KIND_PROBE)


def kind_for_index(idx: int) -> str:
    return {0: KIND_CTL_S2R, 1: KIND_CTL_R2S}.get(idx, KIND_DATA)


class ProtocolError(Exception):
    pass
