from odoo import api, fields, models


class LoyaltyProgram(models.Model):
    """A Netmasha points voucher is one single-use discount-code program.

    Odoo's standard ``promo_code`` program is the only object that gives a fixed
    amount, a minimum order and a usage limit together. The program is tagged so
    that Netmasha can only ever find, expire or archive programs it created.
    """

    _inherit = "loyalty.program"

    x_netmasha_voucher = fields.Boolean(index=True, copy=False, default=False)
    # The exact moment the code stops working. ``date_to`` only knows the day.
    x_netmasha_expires_at = fields.Datetime(copy=False)

    @api.model
    def _netmasha_find(self, code, with_inactive=False):
        """The Netmasha voucher program behind ``code`` (sudo), or an empty recordset."""
        programs = self.sudo()
        if with_inactive:
            programs = programs.with_context(active_test=False)
        return programs.search(
            [("x_netmasha_voucher", "=", True), ("rule_ids.code", "=", code)], limit=1
        )

    def _netmasha_is_expired(self):
        self.ensure_one()
        return bool(self.x_netmasha_expires_at and fields.Datetime.now() >= self.x_netmasha_expires_at)

    @api.model
    def _netmasha_archive_expired(self):
        """Nightly tidy-up (cron): archive vouchers past their expiry so the merchant's
        promotions list does not fill up. Netmasha's own revoke does this at expiry
        too; this catches anything it could not reach."""
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=1)
        self.sudo().search(
            [("x_netmasha_voucher", "=", True), ("x_netmasha_expires_at", "<", cutoff)]
        ).write({"active": False})
