import functools
import re

from odoo import _, api, fields, models
from odoo.http import request

from . import netmasha_sender
from ..utils import netmasha_events


CLICK_RE = re.compile(r"^[a-z0-9]{8,100}$", re.I)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    x_netmasha_click = fields.Char(index=True, copy=False, groups="base.group_system")
    # The Netmasha points vouchers applied to this order, [{code, amount}]. Read by
    # Netmasha's hourly order readback; the paid-order event carries the same list.
    x_netmasha_codes = fields.Json(compute="_compute_x_netmasha_codes", groups="base.group_system")

    def _compute_x_netmasha_codes(self):
        for order in self:
            order.x_netmasha_codes = order._netmasha_voucher_codes()

    def _netmasha_voucher_codes(self):
        """The Netmasha vouchers applied to this order and the discount each one gave."""
        self.ensure_one()
        lines = self.sudo().order_line.filtered(
            lambda line: line.reward_id.program_id.x_netmasha_voucher
        )
        rows = []
        for program in lines.reward_id.program_id:
            program_lines = lines.filtered(lambda line: line.reward_id.program_id == program)
            rows.append({
                "code": program.rule_ids[:1].code,
                "amount": -sum(program_lines.mapped("price_total")),
            })
        return netmasha_events.build_codes(rows)

    def _netmasha_is_reportable(self):
        """Netmasha's order: it carries the click id or was paid with a Netmasha voucher."""
        self.ensure_one()
        return bool(self.sudo().x_netmasha_click or self._netmasha_voucher_codes())

    def _try_apply_code(self, code):
        """Refuse a Netmasha voucher from its exact expiry moment.

        The program's own ``date_to`` is a day, so without this a code could still
        be applied for the rest of the expiry day after Netmasha returned the points.
        """
        program = self.env["loyalty.program"]._netmasha_find(str(code or "").strip().upper())
        if program and program._netmasha_is_expired():
            return {"error": _("This Netmasha coupon has expired.")}
        return super()._try_apply_code(code)

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

    def _action_cancel(self):
        """Report a cancelled attributed order to Netmasha, once the transaction is committed."""
        attributed = self.filtered(lambda order: order.state != "cancel" and order._netmasha_is_reportable())
        # A cancelled confirmed order frees the voucher's single use in Odoo. Netmasha
        # returns the points, so the code is retired: it must not work a second time.
        for order in attributed.filtered(lambda order: order.state in ("sale", "done")):
            programs = order.sudo().order_line.reward_id.program_id.filtered("x_netmasha_voucher")
            programs.write({"x_netmasha_expires_at": fields.Datetime.now()})
        result = super()._action_cancel()
        config = netmasha_sender.settings(self.env) if attributed else None
        if not config:
            return result
        claim = self.env["account.move"]._netmasha_claim
        for order in attributed.filtered(lambda order: order.state == "cancel"):
            payload = netmasha_events.build_cancel_payload({
                "id": order.id,
                "currency": order.currency_id.name or "",
                "cancelled_at": netmasha_sender.iso_datetime(fields.Datetime.now()),
                "x_netmasha_click": order.sudo().x_netmasha_click,
                "x_netmasha_codes": order._netmasha_voucher_codes(),
            })
            event_id = netmasha_events.cancel_event_id(self.env.cr.dbname, order.id)
            if payload and claim(event_id):
                self.env.cr.postcommit.add(
                    functools.partial(
                        netmasha_sender.deliver,
                        config,
                        netmasha_events.TOPIC_ORDER_CANCELLED,
                        event_id,
                        payload,
                    )
                )
        return result

    def _netmasha_order_payload(self):
        """The body Netmasha expects for a paid order.

        Returns ``None`` when the order carries no Netmasha click and no Netmasha
        voucher: an order that has nothing to do with Netmasha is never reported.
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
            "x_netmasha_codes": self._netmasha_voucher_codes(),
        })

    def _netmasha_readback_row(self):
        """One order as Netmasha's hourly readback expects it (the shape ``normalizeOdooOrder`` reads)."""
        self.ensure_one()
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "amount_total": self.amount_total,
            "amount_tax": self.amount_tax,
            "currency_id": [self.currency_id.id, self.currency_id.name or ""],
            "item_count": self._netmasha_item_count(),
            "date_order": self._netmasha_date_order(),
            "x_netmasha_click": self.sudo().x_netmasha_click or "",
            "x_netmasha_codes": self._netmasha_voucher_codes(),
            "paid": any(
                invoice.state == "posted"
                and invoice.move_type == "out_invoice"
                and invoice.payment_state in ("paid", "in_payment")
                for invoice in self.invoice_ids
            ),
        }

    def _netmasha_item_count(self):
        """The real order lines. Sections and notes are not items."""
        return len(self.order_line.filtered(lambda line: not line.display_type))

    def _netmasha_date_order(self):
        """The order date in the format the Netmasha server parses."""
        return netmasha_sender.iso_datetime(self.date_order)
