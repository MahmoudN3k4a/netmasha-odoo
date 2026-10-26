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

import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

DEFAULT_API_BASE = "https://api.netmasha.com"
WEBHOOK_PATH = "/api/webhooks/commerce/odoo/%s"

TOPIC_ORDER_PAID = "order.paid"
TOPIC_REFUND_POSTED = "refund.posted"
TOPIC_ORDER_CANCELLED = "order.cancelled"

# The body of a cancelled order, exactly as the server expects it.
ORDER_CANCEL_PAYLOAD_KEYS = (
    "id",
    "order_id",
    "currency",
    "cancelled_at",
)

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
    "x_netmasha_codes",
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


def cancel_event_id(db_name, order_id):
    """Stable id of the "this order was cancelled" event."""
    return "odoo-%s-sale-%s-cancelled" % (db_slug(db_name), order_id)


def refund_event_id(db_name, move_id):
    """Stable id of the "this credit note was posted" event."""
    return "odoo-%s-refund-%s" % (db_slug(db_name), move_id)


# A Netmasha points voucher is NM + 8..32 letters/digits. Any other discount on an
# order belongs to the merchant and is never reported.
VOUCHER_CODE_RE = re.compile(r"^NM[A-Z0-9]{8,32}$", re.I)


def is_voucher_code(code):
    """True for a code Netmasha issued (and only those)."""
    return bool(VOUCHER_CODE_RE.fullmatch(str(code or "").strip()))


def build_codes(rows):
    """``[{code, amount}]`` for the Netmasha vouchers among ``rows``, nothing else.

    ``amount`` is the discount the order actually applied, in currency units.
    """
    codes = []
    for row in rows or []:
        code = str((row or {}).get("code") or "").strip().upper()
        if not is_voucher_code(code):
            continue
        amount = round(float((row or {}).get("amount") or 0), 2)
        codes.append({"code": code, "amount": amount})
    return codes


def build_order_payload(order):
    """The paid-order body, or ``None`` for an order that is not Netmasha's.

    An order is Netmasha's when it carries a click id or was paid with a
    Netmasha points voucher. ``order`` is a plain mapping with the keys of
    :data:`ORDER_PAYLOAD_KEYS`.
    """
    order = order or {}
    click = str(order.get("x_netmasha_click") or "").strip()
    codes = build_codes(order.get("x_netmasha_codes"))
    if not click and not codes:
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
        "x_netmasha_codes": codes,
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


def build_cancel_payload(order):
    """The cancelled-order body, or ``None`` for an order that is not Netmasha's."""
    order = order or {}
    if not str(order.get("x_netmasha_click") or "").strip() and not build_codes(order.get("x_netmasha_codes")):
        return None
    return {
        "id": order.get("id"),
        "order_id": str(order.get("id")),
        "currency": order.get("currency") or "",
        "cancelled_at": order.get("cancelled_at") or "",
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


# ----------------------------------------------------------------------
# Incoming, signed voucher requests (POST /netmasha/voucher)
# ----------------------------------------------------------------------

MAX_SKEW_SECONDS = 300
NONCE_TTL_SECONDS = 600
NONCE_RE = re.compile(r"^[a-f0-9]{16,64}$")
MAX_VALUE_PST = 100_000_000
MAX_MIN_ORDER_PST = 1_000_000_000


def verify_request(secret, raw_body, signature, timestamp_header, now):
    """Check one incoming request. Returns ``(data, None)`` or ``(None, reason)``.

    The signature is the lowercase hex HMAC-SHA256 of the raw body made with the
    shared webhook secret. The timestamp (unix seconds) is also inside the signed
    body and must equal the header; it must be within five minutes of ``now``.
    The caller still has to burn the nonce so the request cannot be replayed.
    """
    secret = str(secret or "")
    if not secret:
        return None, "not_configured"
    expected = sign(secret, raw_body or b"")
    if not hmac.compare_digest(expected, str(signature or "").strip().lower()):
        return None, "bad_signature"
    try:
        data = json.loads((raw_body or b"").decode("utf-8"))
        stamp = int(data["timestamp"])
        nonce = str(data["nonce"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None, "bad_request"
    if not isinstance(data, dict) or not NONCE_RE.fullmatch(nonce):
        return None, "bad_request"
    if abs(int(now) - stamp) > MAX_SKEW_SECONDS or str(stamp) != str(timestamp_header or "").strip():
        return None, "stale"
    return data, None


def parse_iso_utc(value):
    """A naive UTC datetime from ``2026-10-26T12:00:00.000Z`` (or no fraction), else ``None``."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.\d+)?Z?", text)
    if not match:
        return None
    try:
        parsed = datetime.strptime("%s %s" % match.groups(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc).replace(tzinfo=None)


def parse_voucher_create(data):
    """Validate a ``create`` request. Returns ``(values, None)`` or ``(None, message)``.

    ``values``: ``code`` (upper case), ``amount`` and ``minimum`` in currency
    units, ``expires_at`` (naive UTC datetime) and ``date_to`` (its date).
    """
    data = data or {}
    code = str(data.get("code") or "").strip().upper()
    if not is_voucher_code(code):
        return None, "Invalid code."
    try:
        value = int(data.get("valuePst"))
        minimum = int(data.get("minOrderPst") or 0)
    except (TypeError, ValueError):
        return None, "Invalid voucher."
    expires_at = parse_iso_utc(data.get("expiresAt"))
    if not 1 <= value <= MAX_VALUE_PST or not 0 <= minimum <= MAX_MIN_ORDER_PST or not expires_at:
        return None, "Invalid voucher."
    return {
        "code": code,
        "amount": value / 100.0,
        "minimum": minimum / 100.0,
        "expires_at": expires_at,
        "date_to": expires_at.date(),
    }, None


# ----------------------------------------------------------------------
# One-click "Connect to Netmasha" (R-171)
# ----------------------------------------------------------------------

DEFAULT_PORTAL_BASE = "https://store.netmasha.com"
CONNECT_TTL_SECONDS = 15 * 60
CONNECT_RETURN_PATH = "/netmasha/connect/return"
MAX_READBACK_LIMIT = 200


def new_state():
    """A random value that proves the browser coming back is the one that left."""
    return secrets.token_urlsafe(24)


def new_verifier():
    """The PKCE verifier. It stays on this server; only its hash leaves it."""
    return secrets.token_urlsafe(48)


def pkce_challenge(verifier):
    """base64url(sha256(verifier)) without padding (PKCE S256)."""
    digest = hashlib.sha256(str(verifier).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_connect_url(portal_base, site, return_url, challenge):
    """The Netmasha portal address the Connect button opens."""
    return "%s/connect/odoo?%s" % (
        str(portal_base or DEFAULT_PORTAL_BASE).rstrip("/"),
        urlencode({"site": site, "return": return_url, "challenge": challenge}),
    )


def build_return_url(site, state):
    """Where the portal sends the browser back to; it carries this addon's own state."""
    return "%s%s?%s" % (str(site).rstrip("/"), CONNECT_RETURN_PATH, urlencode({"netmasha_state": state}))


def check_pending(pending, state, uid, now):
    """True when ``pending`` (what Connect stored) matches the returning browser.

    One attempt, same Odoo user, not expired, and the state compares equal in
    constant time.
    """
    if not isinstance(pending, dict) or not state:
        return False
    stored = str(pending.get("state") or "")
    if not stored or not hmac.compare_digest(stored.encode("utf-8"), str(state).encode("utf-8")):
        return False
    if int(pending.get("uid") or 0) != int(uid or -1):
        return False
    return float(pending.get("expires") or 0) > float(now)


def exchange_body(code, verifier, site, db):
    """What the addon sends to ``/api/connect/exchange`` (server to server)."""
    return {"code": str(code), "verifier": str(verifier), "site": str(site), "db": str(db or "")}


def parse_exchange(status, body):
    """The connection to save from the API's answer, or ``None``."""
    if status != 200 or not isinstance(body, dict):
        return None
    store_id = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("storeId") or ""))
    secret = str(body.get("webhookSecret") or "")
    if not store_id or not secret:
        return None
    return {
        "store_id": store_id,
        "secret": secret,
        "store_name": str(body.get("storeName") or "")[:120],
    }


def disconnect_body(store_id, now, nonce=None):
    """The signed body of a disconnect request (the hex signature is made over these exact bytes)."""
    return serialize({
        "storeId": str(store_id),
        "timestamp": int(now),
        "nonce": nonce or secrets.token_hex(16),
    })


def clamp_limit(value, default=100):
    """A page size between 1 and :data:`MAX_READBACK_LIMIT`."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_READBACK_LIMIT, number))


def clamp_offset(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def parse_odoo_datetime(value):
    """``2026-10-26 12:00:00`` or ISO ``...T...Z`` to a naive UTC datetime, else ``None``."""
    return parse_iso_utc(str(value or "").replace(" ", "T", 1)) if value else None
