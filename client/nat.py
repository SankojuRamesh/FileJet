"""NAT traversal helpers: local candidates, STUN (RFC 5389), TCP reflection and hole punching."""
from __future__ import annotations

import ipaddress
import logging
import secrets
import socket
import struct
import time
from dataclasses import dataclass

from .transport import readline_raw

log = logging.getLogger("p2p.nat")

STUN_MAGIC = 0x2112A442


@dataclass
class NatInfo:
    public_ip: str | None = None
    nat_type: str = "unknown"      # "open" | "cone" | "symmetric" | "unknown"
    mapped: tuple | None = None


def primary_ip() -> str | None:
    """Address of the interface holding the default route (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def local_ips(include_ipv6: bool = True) -> list[str]:
    found: list[str] = []
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family in (socket.AF_INET, socket.AF_INET6):
                    found.append(a.address.split("%")[0])
    except Exception:
        try:
            found.extend(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None))
        except OSError:
            pass
    result, seen = [], set()
    first = primary_ip()
    for ip in ([first] if first else []) + found:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if ip in seen or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
            continue
        if addr.version == 6 and (not include_ipv6 or not addr.is_global):
            continue
        seen.add(ip)
        result.append(ip)
    return result


def is_private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback or a.is_link_local
    except ValueError:
        return False


def _parse_hostport(server: str, default_port: int = 3478) -> tuple[str, int]:
    host, _, port = server.rpartition(":")
    if not host:
        return server, default_port
    return host, int(port)


def stun_binding(server: str, sock: socket.socket | None = None, timeout: float = 1.5) -> tuple[str, int] | None:
    host, port = _parse_hostport(server)
    own = sock is None
    if own:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))
    try:
        sock.settimeout(timeout)
        addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
        tid = secrets.token_bytes(12)
        request = struct.pack("!HHI", 0x0001, 0, STUN_MAGIC) + tid
        for _ in range(2):
            sock.sendto(request, addr)
            try:
                data, _src = sock.recvfrom(2048)
            except socket.timeout:
                continue
            if len(data) < 20 or data[8:20] != tid or struct.unpack("!H", data[:2])[0] != 0x0101:
                continue
            pos, mapped = 20, None
            while pos + 4 <= len(data):
                atype, alen = struct.unpack("!HH", data[pos:pos + 4])
                val = data[pos + 4:pos + 4 + alen]
                if atype in (0x0020, 0x8020) and len(val) >= 8 and val[1] == 1:   # XOR-MAPPED-ADDRESS
                    p = struct.unpack("!H", val[2:4])[0] ^ (STUN_MAGIC >> 16)
                    ip = struct.unpack("!I", val[4:8])[0] ^ STUN_MAGIC
                    return socket.inet_ntoa(struct.pack("!I", ip)), p
                if atype == 0x0001 and len(val) >= 8 and val[1] == 1:              # MAPPED-ADDRESS
                    mapped = (socket.inet_ntoa(val[4:8]), struct.unpack("!H", val[2:4])[0])
                pos += 4 + alen + (-alen % 4)
            if mapped:
                return mapped
        return None
    except OSError as exc:
        log.debug("STUN %s failed: %s", server, exc)
        return None
    finally:
        if own:
            sock.close()


def detect_nat(servers: list[str], timeout: float = 1.5) -> NatInfo:
    """Query two STUN servers from one socket; differing mappings => symmetric NAT."""
    if not servers:
        return NatInfo()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    try:
        results = []
        for server in servers[:2]:
            r = stun_binding(server, sock, timeout)
            if r:
                results.append(r)
        if not results:
            return NatInfo()
        public_ip = results[0][0]
        if public_ip in local_ips():
            nat_type = "open"
        elif len(results) >= 2 and results[0] != results[1]:
            nat_type = "symmetric"
        elif len(results) >= 2:
            nat_type = "cone"
        else:
            nat_type = "unknown"
        return NatInfo(public_ip=public_ip, nat_type=nat_type, mapped=results[0])
    finally:
        sock.close()


# ----------------------------------------------------------------- TCP hole punching
def _reuse_socket() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    return s


def reflect(host: str, port: int, timeout: float = 3.0) -> tuple[int, tuple[str, int]]:
    """Bind an ephemeral local port, learn its public mapping from the reflector."""
    addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4]
    s = _reuse_socket()
    try:
        s.bind(("0.0.0.0", 0))
        local_port = s.getsockname()[1]
        s.settimeout(timeout)
        s.connect(addr)
        ip, p = readline_raw(s, 100).decode().split()
        return local_port, (ip, int(p))
    finally:
        s.close()


def punch_connect(local_port: int, remote: tuple[str, int], deadline: float) -> socket.socket | None:
    """TCP simultaneous open: keep connecting from ``local_port`` until the peer's SYN crosses ours."""
    while time.monotonic() < deadline:
        s = _reuse_socket()
        try:
            s.bind(("0.0.0.0", local_port))
        except OSError:
            s.close()
            time.sleep(0.1)
            continue
        s.settimeout(max(0.2, min(1.0, deadline - time.monotonic())))
        try:
            s.connect(remote)
            s.settimeout(None)
            return s
        except OSError:
            s.close()
            time.sleep(0.05)
    return None
