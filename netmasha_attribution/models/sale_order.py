import re

from odoo import api, fields, models
from odoo.http import request

from . import netmasha_sender
from ..utils import netmasha_events


CLICK_RE = re.compile(r"^[a-z0-9]{8,100}$", re.I)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    x_netmasha_click = fields.Char(index=True, copy=False, groups="base.group_system")

    @api.model_create_multi
    def create(self, values_list):
        try:
            click = str(request.session.get("netmasha_click") or "").strip()
        except RuntimeError:
            click = ""
        if CLICK_RE.fullmatch(click):
            for values in values_list:
                values.setdefault("x_netmasha_click", click)
        return super().create(values_list)

    def _netmasha_order_payload(self):
        """The body Netmasha expects for a paid order.

        Returns ``None`` when the order carries no Netmasha click: an order
        that did not come from the mall is never reported.
        """
        self.ensure_one()
        return netmasha_events.build_order_payload({
            "id": self.id,
            "name": self.name,
            "amount_total": self.amount_total,
            "amount_tax": self.amount_tax,
            "currency": self.currency_id.name or "",
            "item_count": self._netmasha_item_count(),
            "date_order": self._netmasha_date_order(),
            "x_netmasha_click": self.sudo().x_netmasha_click,
        })

    def _netmasha_item_count(self):
        """The real order lines. Sections and notes are not items."""
        return len(self.order_line.filtered(lambda line: not line.display_type))

    def _netmasha_date_order(self):
        """The order date in the format the Netmasha server parses."""
        return netmasha_sender.iso_datetime(self.date_order)
