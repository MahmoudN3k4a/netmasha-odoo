"""Pure helpers for the Netmasha commerce webhook.

Nothing here may import Odoo. The payload shape, the event ids and the
signature are the contract with the Netmasha server, so they are kept in
plain Python and unit-tested without a database (see
``integrations/odoo/tests_standalone/test_payloads.py``).

The server side of the contract lives in
``api/src/integrations/commerce-reconciliation.service.ts``:
``normalizeOdooOrder`` and ``normalizeOdooRefund`` read exactly the keys
built below, and ``assertSignature`` compares the ``X-Netmasha-Signature``
header with the hex HMAC-SHA256 of the raw request body.
"""

import hashlib
import hmac
import json
import re
from urllib.parse import quote

DEFAULT_API_BASE = "https://api.netmasha.com"
WEBHOOK_PATH = "/api/webhooks/commerce/odoo/%s"

TOPIC_ORDER_PAID = "order.paid"
TOPIC_REFUND_POSTED = "refund.posted"

# The body of a paid order, exactly as the server expects it.
ORDER_PAYLOAD_KEYS = (
    "id",
    "name",
    "amount_total",
    "amount_tax",
    "currency",
    "item_count",
    "date_order",
    "paid",
    "x_netmasha_click",
)

# The body of a posted credit note, exactly as the server expects it.
REFUND_PAYLOAD_KEYS = (
    "id",
    "x_netmasha_order_id",
    "amount_total",
    "currency",
    "invoice_date",
)

# The server accepts event ids matching this pattern and ignores duplicates.
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9:._-]{1,190}$")

_UNSAFE_DB_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def db_slug(db_name):
    """A database name that is safe to put inside an event id header."""
    slug = _UNSAFE_DB_CHARS.sub("-", str(db_name or "").strip()).strip("-")
    return slug or "db"


def order_event_id(db_name, order_id):
    """Stable id of the "this order was paid" event, so a retry is deduped."""
    return "odoo-%s-sale-%s-paid" % (db_slug(db_name), order_id)


def refund_event_id(db_name, move_id):
    """Stable id of the "this credit note was posted" event."""
    return "odoo-%s-refund-%s" % (db_slug(db_name), move_id)


def build_order_payload(order):
    """The paid-order body, or ``None`` when the order carries no click id.

    ``order`` is a plain mapping with the keys of :data:`ORDER_PAYLOAD_KEYS`.
    """
    order = order or {}
    click = str(order.get("x_netmasha_click") or "").strip()
    if not click:
        return None
    return {
        "id": order.get("id"),
        "name": order.get("name") or "",
        "amount_total": order.get("amount_total") or 0,
        "amount_tax": order.get("amount_tax") or 0,
        "currency": order.get("currency") or "",
        "item_count": int(order.get("item_count") or 0),
        "date_order": order.get("date_order") or "",
        "paid": True,
        "x_netmasha_click": click,
    }


def build_refund_payload(refund):
    """The credit-note body, or ``None`` when the order it belongs to is unknown."""
    refund = refund or {}
    order_id = str(refund.get("x_netmasha_order_id") or "").strip()
    if not order_id:
        return None
    return {
        "id": refund.get("id"),
        "x_netmasha_order_id": order_id,
        "amount_total": refund.get("amount_total") or 0,
        "currency": refund.get("currency") or "",
        "invoice_date": refund.get("invoice_date") or "",
    }


def serialize(payload):
    """The exact bytes that are signed and sent. Serialise once, sign those bytes."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sign(secret, body):
    """Lowercase hex HMAC-SHA256 of the exact bytes."""
    return hmac.new(str(secret).encode("utf-8"), body, hashlib.sha256).hexdigest()


def webhook_url(base_url, store_id):
    """The address the signed event is POSTed to."""
    base = str(base_url or DEFAULT_API_BASE).rstrip("/")
    return "%s%s" % (base, WEBHOOK_PATH % quote(str(store_id or ""), safe=""))


def build_event(secret, topic, event_id, payload, base_url=DEFAULT_API_BASE, store_id=""):
    """Everything needed to send one event: url, headers and the signed body."""
    body = serialize(payload)
    return {
        "url": webhook_url(base_url, store_id),
        "headers": {
            "Content-Type": "application/json",
            "X-Netmasha-Topic": topic,
            "X-Netmasha-Event-Id": event_id,
            "X-Netmasha-Signature": sign(secret, body),
        },
        "body": body,
    }
