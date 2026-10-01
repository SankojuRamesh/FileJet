"""Signaling server configuration (environment variables).

The signaling server never carries file data: it only introduces two signed-in devices to each
other (rendezvous + candidate exchange), reports who is online, relays small end-to-end
encrypted control messages, and offers a TCP address reflector for NAT hole punching.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_STUN = ["stun.l.google.com:19302", "stun.cloudflare.com:3478"]


@dataclass
class Settings:
    host: str = "0.0.0.0"
    port: int = 8765
    reflector_port: int = 8766          # raw TCP "what is my public ip:port" for hole punching; 0 = off
    public_host: str = ""               # host clients use for the reflector ("" = the Host they connected to)
    cloud_jwt_secret: str = ""          # shared with the cloud app; every client needs a cloud-issued token
    stun_servers: list = field(default_factory=lambda: list(DEFAULT_STUN))
    signal_rate_per_min: int = 600
    presence_msg_rate_per_min: int = 1200
    max_rooms: int = 20000
    trust_proxy_headers: bool = False
    ssl_certfile: str = ""
    ssl_keyfile: str = ""


def load_settings() -> Settings:
    stun = os.environ.get("P2P_STUN_SERVERS")
    return Settings(
        host=os.environ.get("P2P_HOST", "0.0.0.0"),
        port=int(os.environ.get("P2P_PORT", 8765)),
        reflector_port=int(os.environ.get("P2P_REFLECTOR_PORT", 8766)),
        public_host=os.environ.get("P2P_PUBLIC_HOST", ""),
        cloud_jwt_secret=os.environ.get("P2P_CLOUD_JWT_SECRET", ""),
        stun_servers=[s.strip() for s in stun.split(",") if s.strip()] if stun is not None else list(DEFAULT_STUN),
        trust_proxy_headers=os.environ.get("P2P_TRUST_PROXY", "0") == "1",
        ssl_certfile=os.environ.get("P2P_SSL_CERT", ""),
        ssl_keyfile=os.environ.get("P2P_SSL_KEY", ""),
    )
