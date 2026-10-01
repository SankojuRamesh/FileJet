"""Raw TCP address reflector used for TCP NAT hole punching.

A client connects from the local port it intends to punch with; the reflector replies
``"<public-ip> <public-port>\\n"`` and closes (server-side close keeps TIME_WAIT on the
server, so the client can immediately re-bind the same local port). No payload is accepted.
"""
from __future__ import annotations

import asyncio
import logging

from .auth import RateLimiter

log = logging.getLogger("p2p.server.reflector")


async def start_reflector(host: str, port: int, rate_per_min: int = 600) -> asyncio.base_events.Server:
    limiter = RateLimiter(rate_per_min)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            peer = writer.get_extra_info("peername") or ("", 0)
            ip, port_ = str(peer[0]), int(peer[1])
            if ip.startswith("::ffff:"):
                ip = ip[7:]
            if limiter.allow(ip):
                writer.write(f"{ip} {port_}\n".encode())
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, host, port)
    log.info("TCP reflector listening on %s:%s", host, port)
    return server
