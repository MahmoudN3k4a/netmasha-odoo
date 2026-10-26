import functools

from odoo import api, fields, models

from . import netmasha_sender
from ..utils import netmasha_events


class AccountMove(models.Model):
    _inherit = "account.move"

    # Filled by the merchant's refund workflow when invoice_origin is not the
    # sale-order name. Netmasha's readback accepts either field.
    x_netmasha_order_id = fields.Char(index=True, copy=False, groups="base.group_system")

    # ------------------------------------------------------------------
    # Netmasha events
    # ------------------------------------------------------------------

    @api.depends("amount_residual", "move_type", "state", "company_id")
    def _compute_payment_state(self):
        """Report a paid customer invoice to Netmasha.

        ``payment_state`` is a stored computed field, so this is the one place
        where Odoo says that an invoice became paid. The report is only queued
        here; it is sent once the transaction is committed.
        """
        super()._compute_payment_state()
        paid = self.filtered(
            lambda move: move.move_type == "out_invoice"
            and move.state == "posted"
            and move.payment_state in ("paid", "in_payment")
        )
        if not paid:
            return
        config = netmasha_sender.settings(self.env)
        if not config:
            return
        for move in paid:
            move._netmasha_report_paid_order(config)

    def action_post(self):
        """Report a posted credit note to Netmasha."""
        result = super().action_post()
        refunds = self.filtered(lambda move: move.move_type == "out_refund" and move.state == "posted")
        if not refunds:
            return result
        config = netmasha_sender.settings(self.env)
        if not config:
            return result
        for move in refunds:
            move._netmasha_report_refund(config)
        return result

    def _netmasha_report_paid_order(self, config):
        """Queue one signed event per attributed sale order behind this invoice."""
        for order in self._netmasha_sale_orders():
            payload = order._netmasha_order_payload()
            if not payload:
                # No Netmasha click and no Netmasha voucher: nothing to report.
                continue
            event_id = netmasha_events.order_event_id(self.env.cr.dbname, order.id)
            if not self._netmasha_claim(event_id):
                continue
            self.env.cr.postcommit.add(
                functools.partial(
                    netmasha_sender.deliver,
                    config,
                    netmasha_events.TOPIC_ORDER_PAID,
                    event_id,
                    payload,
                )
            )

    @api.model_create_multi
    def create(self, vals_list):
        """Remember the sale order a credit note belongs to.

        Netmasha's hourly readback looks for that order id on the credit note
        itself, so it is written while the move is still a draft.
        """
        moves = super().create(vals_list)
        for move in moves:
            if move.move_type != "out_refund" or move.sudo().x_netmasha_order_id:
                continue
            order = move._netmasha_sale_order()
            if order:
                move.sudo().write({"x_netmasha_order_id": str(order.id)})
        return moves

    def _netmasha_report_refund(self, config):
        """Queue the signed refund event of a posted credit note."""
        order = self._netmasha_sale_order()
        if not order or not order._netmasha_is_reportable():
            return
        payload = netmasha_events.build_refund_payload({
            "id": self.id,
            "x_netmasha_order_id": str(order.id),
            "amount_total": self.amount_total,
            "currency": self.currency_id.name or "",
            "invoice_date": netmasha_sender.iso_date(self.invoice_date),
        })
        if not payload:
            return
        event_id = netmasha_events.refund_event_id(self.env.cr.dbname, self.id)
        if not self._netmasha_claim(event_id):
            return
        self.env.cr.postcommit.add(
            functools.partial(
                netmasha_sender.deliver,
                config,
                netmasha_events.TOPIC_REFUND_POSTED,
                event_id,
                payload,
            )
        )

    def _netmasha_readback_row(self):
        """One posted credit note as Netmasha's hourly readback expects it."""
        self.ensure_one()
        order = self._netmasha_sale_order()
        return {
            "id": self.id,
            "name": self.name,
            "amount_total": self.amount_total,
            "currency_id": [self.currency_id.id, self.currency_id.name or ""],
            "invoice_date": netmasha_sender.iso_date(self.invoice_date),
            "invoice_origin": self.invoice_origin or "",
            "x_netmasha_order_id": str(order.id) if order else "",
        }

    def _netmasha_sale_orders(self):
        """The sale orders this document belongs to."""
        self.ensure_one()
        orders = self.env["sale.order"]
        for line in self.line_ids:
            orders |= line.sale_line_ids.order_id
        if not orders:
            orders = self.env["sale.order"].search([("invoice_ids", "in", self.ids)])
        return orders

    def _netmasha_sale_order(self):
        """The sale order this credit note belongs to, if there is one."""
        self.ensure_one()
        explicit = (self.sudo().x_netmasha_order_id or "").strip()
        if explicit.isdigit():
            order = self.env["sale.order"].browse(int(explicit)).exists()
            if order:
                return order
        origin = ""
        if self.reversed_entry_id:
            origin = self.reversed_entry_id.invoice_origin or ""
        origin = origin or self.invoice_origin or ""
        for reference in [part.strip() for part in origin.split(",") if part.strip()]:
            order = self.env["sale.order"].search([("name", "in", [reference])], limit=1)
            if order:
                return order
        source = self.reversed_entry_id or self
        return source.line_ids.sale_line_ids.order_id[:1]

    def _netmasha_claim(self, event_id):
        """Claim an event id for this transaction, so it is never sent twice."""
        claimed = getattr(self.env.cr, "_netmasha_claimed", None)
        if claimed is None:
            claimed = set()
            self.env.cr._netmasha_claimed = claimed
        if event_id in claimed:
            return False
        claimed.add(event_id)
        return True
