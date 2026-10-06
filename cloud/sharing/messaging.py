"""Send one-time codes by SMS or WhatsApp.

Choose the provider with environment variables (server.env):

    P2P_SMS_PROVIDER = twilio | webhook | console      (default: console in DEBUG, otherwise not set up)

    twilio   TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
             TWILIO_SMS_FROM        e.g. +15017122661         (needed for SMS)
             TWILIO_WHATSAPP_FROM   e.g. +14155238886         (needed for WhatsApp)
    webhook  P2P_OTP_WEBHOOK_URL    any gateway: receives POST JSON {"channel", "to", "text", "code"}
             P2P_OTP_WEBHOOK_TOKEN  optional, sent as "Authorization: Bearer <token>"
    console  development only: the message is written to the server log and the dev inbox
"""
import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

log = logging.getLogger("cloud.messaging")
CHANNELS = ("sms", "whatsapp")


class MessagingError(Exception):
    pass


def provider() -> str:
    default = "console" if settings.DEBUG else ""
    return os.environ.get("P2P_SMS_PROVIDER", default).strip().lower()


def available(channel: str) -> bool:
    p = provider()
    if p == "twilio":
        need = "TWILIO_SMS_FROM" if channel == "sms" else "TWILIO_WHATSAPP_FROM"
        return all(os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", need))
    if p == "webhook":
        return bool(os.environ.get("P2P_OTP_WEBHOOK_URL"))
    return p == "console"


def send(channel: str, to: str, text: str, code: str = "") -> None:
    """Raise MessagingError when the message could not be handed to the provider."""
    if channel not in CHANNELS:
        raise MessagingError(f"unknown channel {channel!r}")
    if not available(channel):
        raise MessagingError(f"{'SMS' if channel == 'sms' else 'WhatsApp'} is not set up on this server")
    p = provider()
    if p == "console":
        log.warning("[%s to %s] %s", channel.upper(), to, text)
        return
    if p == "twilio":
        return _twilio(channel, to, text)
    return _webhook(channel, to, text, code)


def _post(req: urllib.request.Request) -> None:
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status >= 300:
                raise MessagingError(f"provider answered {r.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        log.warning("message provider error %s: %s", exc.code, detail)
        raise MessagingError(f"the message provider refused it ({exc.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MessagingError(f"the message provider is not reachable ({exc})") from None


def _twilio(channel: str, to: str, text: str) -> None:
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    if channel == "sms":
        frm, dest = os.environ["TWILIO_SMS_FROM"], to
    else:
        frm, dest = f"whatsapp:{os.environ['TWILIO_WHATSAPP_FROM']}", f"whatsapp:{to}"
    body = urllib.parse.urlencode({"From": frm, "To": dest, "Body": text}).encode()
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    _post(urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=body,
                                 headers={"Authorization": f"Basic {auth}",
                                          "Content-Type": "application/x-www-form-urlencoded"}))


def _webhook(channel: str, to: str, text: str, code: str) -> None:
    headers = {"Content-Type": "application/json"}
    if os.environ.get("P2P_OTP_WEBHOOK_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['P2P_OTP_WEBHOOK_TOKEN']}"
    data = json.dumps({"channel": channel, "to": to, "text": text, "code": code}).encode()
    _post(urllib.request.Request(os.environ["P2P_OTP_WEBHOOK_URL"], data=data, headers=headers))
