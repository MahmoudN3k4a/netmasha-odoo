"""The signed route Netmasha uses to create and remove single-use points vouchers.

Authorisation lives in :mod:`.signed`. The route can only create, find or
archive loyalty programs it tagged itself, and (Netmasha-initiated disconnect)
forget its own connection.
"""

from odoo import http
from odoo.http import request
from odoo.modules.module import get_manifest

from ..models import netmasha_sender
from ..utils import netmasha_events
from .signed import authorise, json_response as _json


class NetmashaVoucherController(http.Controller):
    @http.route("/netmasha/voucher", type="http", auth="public", methods=["POST"], csrf=False)
    def voucher(self, **_kwargs):
        data, error = authorise()
        if error:
            return error

        operation = str(data.get("operation") or "")
        if operation == "capability":
            manifest = get_manifest("netmasha_attribution") or {}
            return _json({
                "ok": True,
                "version": manifest.get("version", ""),
                "loyalty": "loyalty.program" in request.env,
            })
        if operation == "disconnect":
            # Netmasha-initiated (portal) disconnect: forget the store id and secret.
            netmasha_sender.forget_connection(request.env)
            return _json({"ok": True})
        if "loyalty.program" not in request.env:
            return _json({"ok": False, "error": "Odoo Loyalty is not installed.", "retryable": True}, 503)
        if operation == "create":
            return self._create(data)
        if operation == "revoke":
            return self._revoke(data)
        return _json({"ok": False, "error": "Unknown operation."}, 400)

    # ------------------------------------------------------------------

    def _create(self, data):
        values, error = netmasha_events.parse_voucher_create(data)
        if error:
            return _json({"ok": False, "error": error}, 400)
        env = request.env(su=True)
        Program = env["loyalty.program"]
        existing = Program._netmasha_find(values["code"], with_inactive=True)
        if existing:
            return _json({"ok": True, "id": str(existing.id)})
        if env["loyalty.rule"].search_count([("code", "=", values["code"])]):
            return _json({"ok": False, "error": "A different discount already uses this code."}, 409)
        program = Program.create({
            "name": "Netmasha points %s" % values["code"],
            "program_type": "promo_code",
            "trigger": "with_code",
            "applies_on": "current",
            "limit_usage": True,
            "max_usage": 1,
            "date_to": values["date_to"],
            "x_netmasha_voucher": True,
            "x_netmasha_expires_at": values["expires_at"],
            "rule_ids": [(0, 0, {
                "code": values["code"],
                "mode": "with_code",
                "minimum_amount": values["minimum"],
                "minimum_amount_tax_mode": "incl",
                "reward_point_mode": "order",
                "reward_point_amount": 1,
            })],
            "reward_ids": [(0, 0, {
                "reward_type": "discount",
                "discount_mode": "per_order",
                "discount": values["amount"],
                "discount_applicability": "order",
                "required_points": 1,
            })],
        })
        return _json({"ok": True, "id": str(program.id)})

    def _revoke(self, data):
        code = str(data.get("code") or "").strip().upper()
        if not netmasha_events.is_voucher_code(code):
            return _json({"ok": False, "error": "Invalid code."}, 400)
        env = request.env(su=True)
        program = env["loyalty.program"]._netmasha_find(code, with_inactive=True)
        if not program:
            return _json({"ok": True, "result": "gone"})
        used = env["sale.order.line"].search_count([
            ("reward_id.program_id", "=", program.id),
            ("order_id.state", "in", ("sale", "done")),
        ])
        if used:
            return _json({"ok": True, "result": "used"})
        program.write({"active": False})
        return _json({"ok": True, "result": "gone"})
