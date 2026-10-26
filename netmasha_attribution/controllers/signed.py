"""Shared checks for every route Netmasha calls on this shop.

Only Netmasha holds the shared webhook secret, so the HMAC over the raw body is
the whole authorisation: no Odoo user, session or API key is involved. The
timestamp must be fresh and the nonce unused. Only after that may a route use
``sudo()`` (or ``su=True``).
"""

import time

from odoo import fields
from odoo.http import request

from ..models import netmasha_sender
from ..utils import netmasha_events

NONCE_PREFIX = "netmasha_attribution.nonce."


def json_response(body, status=200):
    return request.make_json_response(body, status=status)


def burn_nonce(nonce):
    """Record a nonce once. ``ir.config_parameter.key`` is unique, so a replay
    (even a simultaneous one) fails on the database constraint."""
    params = request.env["ir.config_parameter"].sudo()
    now = fields.Datetime.now()
    stale = params.search([("key", "=like", NONCE_PREFIX + "%")]).filtered(
        lambda row: (now - fields.Datetime.to_datetime(row.value)).total_seconds()
        > netmasha_events.NONCE_TTL_SECONDS
    )
    stale.unlink()
    try:
        with request.env.cr.savepoint():
            params.create({"key": NONCE_PREFIX + nonce, "value": fields.Datetime.to_string(now)})
    except Exception:  # noqa: BLE001 - the unique key is the replay guard
        return False
    return True


def authorise():
    """Returns ``(data, None)`` for a good request or ``(None, response)`` to send back."""
    raw = request.httprequest.get_data() or b""
    secret = netmasha_sender.read_config(request.env)["secret"]
    data, reason = netmasha_events.verify_request(
        secret,
        raw,
        request.httprequest.headers.get("X-Netmasha-Signature"),
        request.httprequest.headers.get("X-Netmasha-Timestamp"),
        time.time(),
    )
    if reason:
        return None, json_response({"ok": False, "error": reason}, 401)
    if not burn_nonce(data["nonce"]):
        return None, json_response({"ok": False, "error": "replay"}, 401)
    return data, None
