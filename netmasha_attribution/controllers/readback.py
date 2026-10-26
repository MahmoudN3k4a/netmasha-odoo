"""Signed read-back routes: Netmasha reads orders, credit notes and the catalogue through
the addon instead of holding an Odoo API key (R-171). Same HMAC, timestamp and nonce as the
voucher route; read-only; ``su=True`` only after the signature passes."""

from datetime import timedelta

from odoo import fields, http
from odoo.http import request

from ..utils import netmasha_events
from .signed import authorise, json_response as _json

DEFAULT_WINDOW_DAYS = 60


def _since(value):
    parsed = netmasha_events.parse_odoo_datetime(value)
    return parsed or (fields.Datetime.now() - timedelta(days=DEFAULT_WINDOW_DAYS))


class NetmashaReadbackController(http.Controller):
    @http.route("/netmasha/readback/orders", type="http", auth="public", methods=["POST"], csrf=False)
    def orders(self, **_kwargs):
        data, error = authorise()
        if error:
            return error
        limit = netmasha_events.clamp_limit(data.get("limit"))
        offset = netmasha_events.clamp_offset(data.get("offset"))
        orders = request.env(su=True)["sale.order"].search(
            [("state", "in", ("sale", "done", "cancel")), ("write_date", ">=", _since(data.get("since")))],
            order="write_date asc, id asc",
            limit=limit + 1,
            offset=offset,
        )
        return _json({
            "ok": True,
            "orders": [order._netmasha_readback_row() for order in orders[:limit]],
            "more": len(orders) > limit,
        })

    @http.route("/netmasha/readback/refunds", type="http", auth="public", methods=["POST"], csrf=False)
    def refunds(self, **_kwargs):
        data, error = authorise()
        if error:
            return error
        limit = netmasha_events.clamp_limit(data.get("limit"))
        offset = netmasha_events.clamp_offset(data.get("offset"))
        moves = request.env(su=True)["account.move"].search(
            [("move_type", "=", "out_refund"), ("state", "=", "posted"), ("write_date", ">=", _since(data.get("since")))],
            order="write_date asc, id asc",
            limit=limit + 1,
            offset=offset,
        )
        return _json({
            "ok": True,
            "refunds": [move._netmasha_readback_row() for move in moves[:limit]],
            "more": len(moves) > limit,
        })

    @http.route("/netmasha/readback/products", type="http", auth="public", methods=["POST"], csrf=False)
    def products(self, **_kwargs):
        data, error = authorise()
        if error:
            return error
        limit = netmasha_events.clamp_limit(data.get("limit"))
        domain = [("sale_ok", "=", True)]
        ids = data.get("ids")
        if isinstance(ids, list):
            domain.append(("id", "in", [int(i) for i in ids[:50] if str(i).isdigit()]))
        else:
            try:
                domain.append(("id", ">", int(data.get("cursor") or 0)))
            except (TypeError, ValueError):
                domain.append(("id", ">", 0))
        templates = request.env(su=True)["product.template"].search(domain, order="id asc", limit=limit + 1)
        page = templates[:limit]
        rows = [{
            "id": template.id,
            "name": template.name or "",
            "description_sale": template.description_sale or "",
            "list_price": template.list_price,
            "currency_id": [template.currency_id.id, template.currency_id.name] if template.currency_id else False,
            "default_code": template.default_code or "",
            "website_url": getattr(template, "website_url", "") or "",
            "active": template.active,
        } for template in page]
        return _json({
            "ok": True,
            "products": rows,
            "next": page[-1].id if len(templates) > limit and page else None,
        })
