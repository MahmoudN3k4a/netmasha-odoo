import re

from odoo import http
from odoo.http import request


CLICK_RE = re.compile(r"^[a-z0-9]{8,100}$", re.I)


class NetmashaAttributionController(http.Controller):
    @http.route("/netmasha/attribution", type="json", auth="public", methods=["POST"], csrf=False)
    def capture(self, vm_click=None):
        click = str(vm_click or "").strip()
        if not CLICK_RE.fullmatch(click):
            return {"ok": False}
        request.session["netmasha_click"] = click
        order = request.website.sale_get_order()
        if order and not order.x_netmasha_click:
            order.sudo().write({"x_netmasha_click": click})
        return {"ok": True}
