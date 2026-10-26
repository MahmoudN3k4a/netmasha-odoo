import json
import time

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from . import netmasha_sender
from ..utils import netmasha_events

PARAM_API_BASE = "netmasha_attribution.api_base"
PARAM_STORE_ID = "netmasha_attribution.store_id"
PARAM_SECRET = "netmasha_attribution.webhook_secret"
PARAM_REALTIME = "netmasha_attribution.realtime"

DEFAULT_API_BASE = "https://api.netmasha.com"


class ResConfigSettings(models.TransientModel):
    """Store-facing Netmasha settings (Settings -> Netmasha).

    Only ``base.group_system`` may read or write them: the settings form
    itself is already administrator-only, and every field repeats the group
    so a read or write from any other user is refused by the ORM.
    """

    _inherit = "res.config.settings"

    netmasha_api_base = fields.Char(
        string="Netmasha API base URL",
        config_parameter=PARAM_API_BASE,
        default=DEFAULT_API_BASE,
        groups="base.group_system",
        help="Address of your Netmasha server. Only change it if you run your own installation.",
    )
    netmasha_store_id = fields.Char(
        string="Netmasha store id",
        config_parameter=PARAM_STORE_ID,
        groups="base.group_system",
        help="The store id Netmasha gave you. It is part of your Netmasha store URL.",
    )
    netmasha_webhook_secret = fields.Char(
        string="Webhook secret",
        groups="base.group_system",
        help="Write only. The secret is never shown again after you save it; "
             "leave this field empty to keep the saved one.",
    )
    netmasha_webhook_secret_set = fields.Boolean(
        string="A webhook secret is saved",
        compute="_compute_netmasha_webhook_secret_set",
        groups="base.group_system",
    )
    netmasha_realtime = fields.Boolean(
        string="Send events in real time",
        default=True,
        groups="base.group_system",
        help="When on, a paid invoice and a posted credit note are reported to "
             "Netmasha as soon as they happen. When off, only Netmasha's hourly "
             "readback reports them.",
    )

    netmasha_connected = fields.Boolean(
        string="Connected to Netmasha",
        compute="_compute_netmasha_connection",
        groups="base.group_system",
    )
    netmasha_store_name = fields.Char(
        string="Netmasha store",
        compute="_compute_netmasha_connection",
        groups="base.group_system",
    )

    def _compute_netmasha_connection(self):
        parameters = self.env["ir.config_parameter"].sudo()
        connected = netmasha_sender.is_connected(self.env)
        name = parameters.get_param(netmasha_sender.PARAM_STORE_NAME) or ""
        for record in self:
            record.netmasha_connected = connected
            record.netmasha_store_name = name

    def action_netmasha_connect(self):
        """Connect to Netmasha: open the portal, where the merchant signs in and approves.

        A random state and a PKCE verifier stay in a system parameter for fifteen
        minutes; only the verifier's hash leaves this server. Nothing is copied or
        pasted by the merchant.
        """
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only an administrator can connect this shop to Netmasha."))
        parameters = self.env["ir.config_parameter"].sudo()
        site = (parameters.get_param("web.base.url") or "").rstrip("/")
        state = netmasha_events.new_state()
        verifier = netmasha_events.new_verifier()
        parameters.set_param(
            netmasha_sender.PARAM_PENDING,
            json.dumps({
                "state": state,
                "verifier": verifier,
                "uid": self.env.uid,
                "expires": time.time() + netmasha_events.CONNECT_TTL_SECONDS,
            }),
        )
        url = netmasha_events.build_connect_url(
            parameters.get_param(netmasha_sender.PARAM_PORTAL_BASE) or netmasha_events.DEFAULT_PORTAL_BASE,
            site,
            netmasha_events.build_return_url(site, state),
            netmasha_events.pkce_challenge(verifier),
        )
        return {"type": "ir.actions.act_url", "url": url, "target": "self"}

    def action_netmasha_disconnect(self):
        """Tell Netmasha (signed), then forget the connection here whatever it answers."""
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only an administrator can disconnect this shop from Netmasha."))
        config = netmasha_sender.read_config(self.env)
        if config["store_id"] and config["secret"]:
            body = netmasha_events.disconnect_body(config["store_id"], time.time())
            netmasha_sender.post_signed(
                "%s/api/connect/disconnect" % config["api_base"].rstrip("/"),
                body,
                {"X-Netmasha-Signature": netmasha_events.sign(config["secret"], body)},
            )
        netmasha_sender.forget_connection(self.env)
        return {"type": "ir.actions.client", "tag": "reload"}

    def _compute_netmasha_webhook_secret_set(self):
        saved = bool(self.env["ir.config_parameter"].sudo().get_param(PARAM_SECRET))
        for record in self:
            record.netmasha_webhook_secret_set = saved

    def get_values(self):
        """Read the real-time flag.

        Odoo 17 reads a boolean ``config_parameter`` back with ``bool(value)``,
        so a stored "False" would come back as True. The flag is therefore
        stored as "1"/"0" and translated here.
        """
        values = super().get_values()
        values["netmasha_realtime"] = self._netmasha_realtime_param() == "1"
        return values

    def set_values(self):
        """Save the settings, including the write-only webhook secret."""
        super().set_values()
        self.env["ir.config_parameter"].sudo().set_param(
            PARAM_REALTIME, "1" if self.netmasha_realtime else "0"
        )
        secret = (self.netmasha_webhook_secret or "").strip()
        if secret:
            self.env["ir.config_parameter"].sudo().set_param(PARAM_SECRET, secret)
            # Never keep the clear secret in the transient record.
            self.netmasha_webhook_secret = False

    @api.model
    def _netmasha_realtime_param(self):
        value = self.env["ir.config_parameter"].sudo().get_param(PARAM_REALTIME)
        if value is None:
            return "1"
        return str(value).strip()
