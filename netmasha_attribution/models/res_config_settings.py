from odoo import api, fields, models

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
