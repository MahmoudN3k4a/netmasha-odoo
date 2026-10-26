"""The browser's way back from Netmasha after "Connect to Netmasha" (R-171)."""

import json
import time

from odoo import http
from odoo.http import request

from ..models import netmasha_sender
from ..utils import netmasha_events

SETTINGS = "/web#action=base_setup.action_general_configuration"


class NetmashaConnectController(http.Controller):
    @http.route("/netmasha/connect/return", type="http", auth="user", methods=["GET"])
    def connect_return(self, netmasha_code=None, netmasha_state=None, **_kwargs):
        """Check the state, trade the one-time code and the verifier for the store id and
        secret (server to server), save them, and send the admin back to Settings with a
        clean address bar. Only a Settings administrator may complete a connection."""
        user = request.env.user
        response = request.redirect(SETTINGS)
        response.headers["Referrer-Policy"] = "no-referrer"
        if not user.has_group("base.group_system"):
            return response
        parameters = request.env["ir.config_parameter"].sudo()
        try:
            pending = json.loads(parameters.get_param(netmasha_sender.PARAM_PENDING) or "{}")
        except ValueError:
            pending = {}
        parameters.set_param(netmasha_sender.PARAM_PENDING, False)  # one attempt per Connect click
        if not netmasha_code or not netmasha_events.check_pending(pending, netmasha_state, user.id, time.time()):
            return response
        config = netmasha_sender.read_config(request.env)
        site = (parameters.get_param("web.base.url") or "").rstrip("/")
        status, body = netmasha_sender.post_json(
            "%s/api/connect/exchange" % config["api_base"].rstrip("/"),
            netmasha_events.exchange_body(netmasha_code, pending.get("verifier"), site, request.env.cr.dbname),
        )
        connection = netmasha_events.parse_exchange(status, body)
        if connection:
            parameters.set_param(netmasha_sender.PARAM_STORE_ID, connection["store_id"])
            parameters.set_param(netmasha_sender.PARAM_SECRET, connection["secret"])
            parameters.set_param(netmasha_sender.PARAM_STORE_NAME, connection["store_name"])
            parameters.set_param(netmasha_sender.PARAM_CONNECTED_AT, str(int(time.time())))
        return response
