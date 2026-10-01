"""Minimal UPnP-IGD client: ask the home router to forward a TCP port to us (best effort).

With a mapping in place the peer can connect straight to our public address, which is the
most reliable way to get a *direct* path through a consumer NAT. Everything here has short
timeouts and silently gives up if no IGD answers.
"""
from __future__ import annotations

import logging
import re
import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

log = logging.getLogger("p2p.upnp")

SSDP_ADDR = ("239.255.255.250", 1900)
SERVICES = (
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
)


@dataclass
class PortMapping:
    external_ip: str
    external_port: int
    internal_port: int
    control_url: str
    service: str

    def remove(self) -> None:
        try:
            _soap(self.control_url, self.service, "DeletePortMapping",
                  {"NewRemoteHost": "", "NewExternalPort": self.external_port, "NewProtocol": "TCP"})
        except Exception as exc:
            log.debug("UPnP DeletePortMapping failed: %s", exc)


def _discover(timeout: float) -> list[str]:
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
           "MX: 2\r\nST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n").encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.settimeout(timeout)
    locations = []
    try:
        s.sendto(msg, SSDP_ADDR)
        while True:
            data, _ = s.recvfrom(4096)
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m and m.group(1).decode() not in locations:
                locations.append(m.group(1).decode())
    except OSError:
        pass
    finally:
        s.close()
    return locations


def _find_service(location: str, timeout: float) -> tuple[str, str] | None:
    with urllib.request.urlopen(location, timeout=timeout) as resp:   # noqa: S310 (LAN device URL)
        root = ET.fromstring(resp.read(1 << 20))
    for svc in root.iter("{*}service"):
        stype = (svc.findtext("{*}serviceType") or "").strip()
        if stype in SERVICES:
            ctrl = (svc.findtext("{*}controlURL") or "").strip()
            return urllib.parse.urljoin(location, ctrl), stype
    return None


def _soap(control_url: str, service: str, action: str, args: dict, timeout: float = 3.0) -> str:
    body = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    envelope = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                f'<u:{action} xmlns:u="{service}">{body}</u:{action}></s:Body></s:Envelope>').encode()
    req = urllib.request.Request(control_url, data=envelope, method="POST", headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{service}#{action}"'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310
        return resp.read(1 << 20).decode(errors="replace")


def add_port_mapping(internal_port: int, internal_ip: str | None, lease: int = 3600,
                     timeout: float = 2.0) -> PortMapping | None:
    if not internal_ip:
        return None
    try:
        for location in _discover(timeout):
            try:
                found = _find_service(location, timeout)
            except Exception:
                continue
            if not found:
                continue
            ctrl, service = found
            _soap(ctrl, service, "AddPortMapping", {
                "NewRemoteHost": "", "NewExternalPort": internal_port, "NewProtocol": "TCP",
                "NewInternalPort": internal_port, "NewInternalClient": internal_ip, "NewEnabled": 1,
                "NewPortMappingDescription": "mediarush", "NewLeaseDuration": lease})
            text = _soap(ctrl, service, "GetExternalIPAddress", {})
            m = re.search(r"<NewExternalIPAddress>([^<]+)</NewExternalIPAddress>", text)
            if m:
                log.info("UPnP mapped TCP %s -> %s:%s", internal_port, m.group(1), internal_port)
                return PortMapping(m.group(1), internal_port, internal_port, ctrl, service)
    except Exception as exc:
        log.debug("UPnP failed: %s", exc)
    return None
