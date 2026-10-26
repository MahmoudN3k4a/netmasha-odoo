"""Sends the signed Netmasha events from Odoo.

The HTTP call is deliberately small and never raises: a shop must not fail
because Netmasha is unreachable. Netmasha's hourly readback picks up
anything that was missed.
"""

import logging
import urllib.error
import urllib.request

from odoo import fields

from ..utils import netmasha_events

try:  # Odoo ships requests; the stdlib is the fallback for trimmed installs.
    import requests
except ImportError:  # pragma: no cover
    requests = None

_logger = logging.getLogger(__name__)

PARAM_API_BASE = "netmasha_attribution.api_base"
PARAM_STORE_ID = "netmasha_attribution.store_id"
PARAM_SECRET = "netmasha_attribution.webhook_secret"
PARAM_REALTIME = "netmasha_attribution.realtime"

DEFAULT_API_BASE = netmasha_events.DEFAULT_API_BASE
TIMEOUT = 10


def read_config(env):
    """The Netmasha configuration, read before the transaction commits."""
    parameters = env["ir.config_parameter"].sudo()
    return {
        "api_base": (parameters.get_param(PARAM_API_BASE) or DEFAULT_API_BASE).strip(),
        "store_id": (parameters.get_param(PARAM_STORE_ID) or "").strip(),
        "secret": (parameters.get_param(PARAM_SECRET) or "").strip(),
    }


def realtime_enabled(env):
    """True unless the merchant turned the real-time reports off."""
    value = (env["ir.config_parameter"].sudo().get_param(PARAM_REALTIME) or "1").strip().lower()
    return value in ("1", "true", "yes", "on")


def settings(env):
    """The configuration to report with, or ``None`` when reporting is off.

    Read once per batch, before the transaction commits: the post-commit
    callback must not touch the database.
    """
    if not realtime_enabled(env):
        return None
    config = read_config(env)
    if not config["secret"] or not config["store_id"]:
        return None
    return config


def iso_datetime(value):
    """A UTC timestamp in the format the Netmasha server parses."""
    if not value:
        return ""
    return fields.Datetime.to_datetime(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_date(value):
    """A date in the format the Netmasha server parses."""
    if not value:
        return ""
    return fields.Date.to_string(value)


def deliver(config, topic, event_id, payload):
    """POST one signed event. Returns True when Netmasha accepted it.

    A failure is logged without the secret and without the body, and never
    raises.
    """
    try:
        event = netmasha_events.build_event(
            config["secret"],
            topic,
            event_id,
            payload,
            config["api_base"],
            config["store_id"],
        )
        status = _post(event["url"], event["body"], event["headers"])
    except Exception as error:  # noqa: BLE001 - a report must never break the shop
        _logger.warning(
            "Netmasha: the %s event %s was not delivered (%s).", topic, event_id, error
        )
        return False
    if status >= 300:
        _logger.warning(
            "Netmasha: the %s event %s was rejected (HTTP %s).", topic, event_id, status
        )
        return False
    return True


def _post(url, body, headers):
    """Send the exact bytes that were signed."""
    if requests is not None:
        response = requests.post(url, data=body, headers=headers, timeout=TIMEOUT)
        return response.status_code
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


# ----------------------------------------------------------------------
# One-click connect
# ----------------------------------------------------------------------

PARAM_PENDING = "netmasha_attribution.pending_connect"
PARAM_STORE_NAME = "netmasha_attribution.store_name"
PARAM_CONNECTED_AT = "netmasha_attribution.connected_at"
PARAM_PORTAL_BASE = "netmasha_attribution.portal_base"


def is_connected(env):
    """True when a store id and a secret are saved."""
    config = read_config(env)
    return bool(config["store_id"] and config["secret"])


def post_json(url, payload):
    """POST ``payload`` as JSON. Returns ``(status, body_dict)``; never raises.

    Used for the server-to-server calls of the Connect flow. The body is never
    logged: it carries a one-time code and the verifier.
    """
    import json

    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    try:
        if requests is not None:
            response = requests.post(url, data=data, headers=headers, timeout=TIMEOUT)
            status, text = response.status_code, response.text
        else:
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    status, text = response.status, response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as error:
                status, text = error.code, error.read().decode("utf-8", "replace")
        try:
            body = json.loads(text)
        except ValueError:
            body = {}
        return status, body if isinstance(body, dict) else {}
    except Exception as error:  # noqa: BLE001 - a connect attempt must fail softly
        _logger.warning("Netmasha: a connect request could not be sent (%s).", type(error).__name__)
        return 0, {}


def forget_connection(env):
    """Drop the store id, the secret and the pending connect attempt."""
    parameters = env["ir.config_parameter"].sudo()
    for key in (PARAM_STORE_ID, PARAM_SECRET, PARAM_STORE_NAME, PARAM_CONNECTED_AT, PARAM_PENDING):
        parameters.set_param(key, False)


def post_signed(url, body, headers):
    """POST the exact signed ``body`` bytes. Returns the HTTP status, or 0; never raises."""
    merged = {"Content-Type": "application/json"}
    merged.update(headers)
    try:
        return _post(url, body, merged)
    except Exception as error:  # noqa: BLE001
        _logger.warning("Netmasha: a signed request could not be sent (%s).", type(error).__name__)
        return 0
