"""Connection establishment: DIRECT -> TCP HOLE PUNCH. File data NEVER passes through a server.

Both peers run this in lockstep, exchanging results through the signaling server, and make
the same deterministic decision at every stage, so no extra "who wins" round-trip is needed.

    1. hello   : certificates, candidate addresses (LAN, IPv6, UPnP, STUN), NAT type, policy
    2. direct  : each side dials all of the other's candidates (TLS + HELLO probe, ~4 s)
                 -> if the sender reached the receiver, the sender dials all streams, else
                    if the receiver reached the sender, the receiver dials.
    3. punch   : both learn per-stream public TCP mappings from the reflector and perform
                 TCP simultaneous open (works on endpoint-independent-mapping NATs)

There is deliberately no relay: if neither a direct nor a hole-punched path exists (e.g. both
peers behind symmetric / carrier-grade NAT), the transfer waits and retries; the fix is a port
forward or UPnP on one side (Settings), or IPv6.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from . import nat, upnp
from .config import ClientConfig
from .identity import Identity, fingerprint_of_pem, short_auth_string
from .protocol import KIND_PROBE, kind_for_index
from .signaling_client import SignalingClient
from .transport import (Acceptor, AuthenticationError, Channel, StreamAuth, dial, secure_stream,
                        tune_socket)

log = logging.getLogger("p2p.session")

CTL_IN_TIMEOUT = 20.0      # liveness: peers send something at least every second
CTL_OUT_TIMEOUT = 60.0
DATA_TIMEOUT = 300.0


class EstablishError(Exception):
    pass


@dataclass
class Connection:
    ctl_out: Channel
    ctl_in: Channel
    data: list
    method: str               # "direct" | "punch"
    conn_type: str            # what the UI shows
    remote: str
    handshake_ms: float | None
    peer_fp: str
    sas: str
    peer_nat: str = "unknown"
    local_nat: str = "unknown"
    extra: dict = field(default_factory=dict)

    def channels(self) -> list:
        return [self.ctl_out, self.ctl_in, *self.data]

    def close(self) -> None:
        for ch in self.channels():
            ch.close()


def connection_label(method: str, host: str) -> str:
    if method == "punch":
        return "DIRECT P2P (hole punched)"
    return "DIRECT P2P (LAN)" if nat.is_private(host) else "DIRECT P2P"


def _valid_addr(c) -> bool:
    return (isinstance(c, dict) and isinstance(c.get("ip"), str) and isinstance(c.get("port"), int)
            and 0 < c["port"] < 65536)


def gather_candidates(cfg: ClientConfig, port: int, nat_info: nat.NatInfo, mapping, observed_ip) -> list[dict]:
    cands = []
    for ip in nat.local_ips():
        cands.append({"ip": ip, "port": port, "type": "host"})
    if mapping is not None:
        cands.append({"ip": mapping.external_ip, "port": mapping.external_port, "type": "upnp"})
    if cfg.public_addr:
        host, _, p = cfg.public_addr.rpartition(":")
        cands.append({"ip": host, "port": int(p), "type": "manual"})
    public = nat_info.public_ip or observed_ip
    if public and cfg.listen_port and not nat.is_private(public):
        # A user who pinned --port may have forwarded it on the router.
        cands.append({"ip": public, "port": port, "type": "srflx"})
    if cfg.include_loopback:
        cands.append({"ip": "127.0.0.1", "port": port, "type": "loopback"})
    seen, out = set(), []
    for c in cands:
        key = (c["ip"], c["port"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out[:16]


def _parallel(fn, items, timeout: float) -> list:
    results = [None] * len(items)

    def run(i, item):
        try:
            results[i] = fn(item)
        except Exception as exc:
            log.debug("parallel task failed: %s", exc)

    threads = [threading.Thread(target=run, args=(i, it), daemon=True) for i, it in enumerate(items)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    return results


def probe_candidates(cands: list[dict], auth: StreamAuth, timeout: float, sock_buf) -> list[dict]:
    def attempt(c):
        t0 = time.monotonic()
        raw = dial(c["ip"], c["port"], min(3.0, timeout), sock_buf)
        ch = secure_stream(raw, auth, initiator=True, idx=-1, kind=KIND_PROBE, timeout=timeout)
        ch.close()
        return {"ip": c["ip"], "port": c["port"], "type": c.get("type"),
                "ms": round((time.monotonic() - t0) * 1000, 2)}

    # Return shortly after the first success instead of waiting for unreachable candidates to time out
    # (matters for folder transfers, where every file sets up its own connection).
    results: list = []
    first = threading.Event()
    lock = threading.Lock()

    def run(c):
        try:
            r = attempt(c)
        except Exception as exc:
            log.debug("probe %s failed: %s", c.get("ip"), exc)
            return
        with lock:
            results.append(r)
        first.set()

    threads = [threading.Thread(target=run, args=(c,), daemon=True) for c in cands]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout + 0.5
    if first.wait(max(0.0, deadline - time.monotonic())):
        grace = min(deadline, time.monotonic() + 0.3)
        for t in threads:
            t.join(max(0.0, grace - time.monotonic()))
    with lock:
        ok = sorted(results, key=lambda r: r["ms"])
    return ok


def _assemble(channels: list, method: str, host: str, remote: str, **kw) -> Connection:
    by_idx = {}
    for ch in channels:
        if ch is None:
            continue
        if ch.idx in by_idx:
            ch.close()
        else:
            by_idx[ch.idx] = ch
    if 0 not in by_idx or 1 not in by_idx or not any(i >= 2 for i in by_idx):
        for ch in by_idx.values():
            ch.close()
        raise EstablishError(f"{method}: not enough streams established ({len(by_idx)})")
    data = [by_idx[i] for i in sorted(by_idx) if i >= 2]
    return Connection(ctl_out=None, ctl_in=None, data=data, method=method,
                      conn_type=connection_label(method, host), remote=remote,
                      handshake_ms=kw.get("handshake_ms"), peer_fp="", sas="",
                      extra={"by_idx": by_idx})


def establish(role: str, sig: SignalingClient, paired: dict, identity: Identity, cfg: ClientConfig,
              auth_key: bytes, cancel: threading.Event | None = None,
              expected_peer_fp: str | None = None) -> Connection:
    acceptor = Acceptor(cfg.listen_port, cfg.sock_buf)
    mapping = None
    try:
        box: dict = {}
        jobs = []
        if cfg.use_stun and cfg.stun_servers:
            jobs.append(threading.Thread(target=lambda: box.__setitem__("nat", nat.detect_nat(cfg.stun_servers)),
                                         daemon=True))
        if cfg.use_upnp and cfg.allow_direct:
            jobs.append(threading.Thread(
                target=lambda: box.__setitem__("upnp", upnp.add_port_mapping(acceptor.port, nat.primary_ip())),
                daemon=True))
        for j in jobs:
            j.start()
        for j in jobs:
            j.join(4.0)
        nat_info = box.get("nat") or nat.NatInfo()
        mapping = box.get("upnp")
        cands = gather_candidates(cfg, acceptor.port, nat_info, mapping, paired.get("observed_ip"))
        policy = {"direct": cfg.allow_direct, "punch": cfg.allow_punch}

        sig.send_signal("hello", {"v": 1, "cert": identity.cert_pem, "fp": identity.fingerprint,
                                  "cands": cands, "nat": nat_info.nat_type, "policy": policy,
                                  "max_data": cfg.max_streams})
        peer = sig.recv_signal("hello", 60, cancel)
        peer_pem = str(peer.get("cert", ""))
        try:
            peer_fp = fingerprint_of_pem(peer_pem)
        except Exception:
            raise AuthenticationError("peer sent an invalid certificate") from None
        if peer_fp != peer.get("fp"):
            raise AuthenticationError("peer fingerprint mismatch")
        if expected_peer_fp and peer_fp != expected_peer_fp:
            raise AuthenticationError("peer identity differs from the one this transfer was started with "
                                      "(possible man-in-the-middle) - refusing to resume")
        auth = StreamAuth(role=role, identity=identity, peer_cert_pem=peer_pem, peer_fp=peer_fp,
                          auth_key=auth_key, session_id=str(paired["session_id"]))
        acceptor.set_auth(auth)
        peer_policy = peer.get("policy") or {}
        eff = {k: bool(policy[k] and peer_policy.get(k, False)) for k in policy}
        n_data = max(1, min(cfg.max_streams, int(peer.get("max_data") or 1), 32))
        total = n_data + 2
        peer_cands = [c for c in (peer.get("cands") or []) if _valid_addr(c)][:16]
        conn: Connection | None = None
        errors = []

        # ------------------------------------------------------------ direct
        mine = probe_candidates(peer_cands, auth, cfg.direct_timeout, cfg.sock_buf) if eff["direct"] else []
        sig.send_signal("direct", {"ok": mine})
        theirs = [c for c in (sig.recv_signal("direct", cfg.direct_timeout + 30, cancel).get("ok") or [])
                  if _valid_addr(c)]
        s_ok, r_ok = (mine, theirs) if role == "sender" else (theirs, mine)
        choice = ("sender", s_ok[0]) if s_ok else ("receiver", r_ok[0]) if r_ok else None
        if choice is not None:
            dialer, target = choice
            remote = f"{target['ip']}:{target['port']}"
            if dialer == role:
                def open_stream(i):
                    raw = dial(target["ip"], target["port"], 10.0, cfg.sock_buf)
                    return secure_stream(raw, auth, initiator=True, idx=i, kind=kind_for_index(i), remote=remote)
                channels = _parallel(open_stream, list(range(total)), 20.0)
            else:
                channels = acceptor.collect(total, 20.0)
            try:
                conn = _assemble(channels, "direct", target["ip"], remote, handshake_ms=target.get("ms"))
            except EstablishError as exc:        # probe worked but streams did not: try the next rung
                errors.append(str(exc))
        else:
            errors.append("direct: no candidate reachable")

        # ------------------------------------------------------- hole punching
        reflector = paired.get("reflector")
        peer_nat = str(peer.get("nat", "unknown"))
        can_punch = (conn is None and eff["punch"] and reflector
                     and "symmetric" not in (nat_info.nat_type, peer_nat))
        if can_punch:
            conn = _punch(role, sig, auth, reflector, total, cfg, cancel)
            if conn is None:
                errors.append("punch: failed")

        if conn is None:
            raise EstablishError("no direct connection possible yet (" + "; ".join(errors) + "). Both computers "
                                 "are behind strict NATs: enable UPnP or forward a port (Settings) on one side")

        by_idx = conn.extra.pop("by_idx")
        s2r, r2s = by_idx[0], by_idx[1]
        conn.ctl_out, conn.ctl_in = (s2r, r2s) if role == "sender" else (r2s, s2r)
        conn.ctl_in.settimeout(CTL_IN_TIMEOUT)
        conn.ctl_out.settimeout(CTL_OUT_TIMEOUT)
        for ch in conn.data:
            ch.settimeout(DATA_TIMEOUT)
        conn.peer_fp = peer_fp
        fs, fr = auth.fingerprints
        conn.sas = short_auth_string(fs, fr)
        conn.local_nat, conn.peer_nat = nat_info.nat_type, peer_nat
        log.info("connected: %s via %s (%d data streams)", conn.conn_type, conn.remote, len(conn.data))
        return conn
    finally:
        acceptor.close()
        if mapping is not None:
            threading.Thread(target=mapping.remove, daemon=True).start()


def _punch(role, sig, auth, reflector, total, cfg, cancel) -> Connection | None:
    try:
        mapped = _parallel(lambda _i: nat.reflect(reflector["host"], int(reflector["port"])),
                           list(range(total)), 6.0)
        if any(m is None for m in mapped):
            mapped = None
    except Exception:
        mapped = None
    sig.send_signal("punch", {"addrs": [[lp, ip, p] for lp, (ip, p) in mapped] if mapped else None})
    peer_addrs = sig.recv_signal("punch", 30, cancel).get("addrs")
    if not mapped or not isinstance(peer_addrs, list) or len(peer_addrs) != total:
        return None
    deadline = time.monotonic() + cfg.punch_timeout

    def punch_one(i):
        lp = mapped[i][0]
        _, rip, rport = peer_addrs[i]
        raw = nat.punch_connect(lp, (str(rip), int(rport)), deadline)
        if raw is None:
            return None
        tune_socket(raw, cfg.sock_buf)
        return secure_stream(raw, auth, initiator=role == "sender", idx=i, kind=kind_for_index(i),
                             remote=f"{rip}:{rport}")

    channels = _parallel(punch_one, list(range(total)), cfg.punch_timeout + 15)
    ok = [ch.idx for ch in channels if ch is not None]
    sig.send_signal("punch_ok", {"ok": ok})
    peer_ok = set(sig.recv_signal("punch_ok", 30, cancel).get("ok") or [])
    keep = [ch for ch in channels if ch is not None and ch.idx in peer_ok]
    for ch in channels:
        if ch is not None and ch not in keep:
            ch.close()
    try:
        return _assemble(keep, "punch", str(peer_addrs[0][1]), f"{peer_addrs[0][1]}:{peer_addrs[0][2]}")
    except EstablishError:
        return None
