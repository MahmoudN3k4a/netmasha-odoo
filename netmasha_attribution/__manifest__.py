{
    "name": "Netmasha Attribution",
    "summary": "Keeps the Netmasha click id on your Odoo orders and reports paid orders and refunds to Netmasha in real time.",
    "description": """
Netmasha Attribution connects this Odoo shop to your Netmasha account.

- It captures the Netmasha click id a shopper carries from the Netmasha mall
  and keeps it on the sale order.
- When a customer invoice for such an order becomes paid, the addon sends a
  signed event to Netmasha.
- When a credit note is posted for such an order, the addon sends a signed
  refund event to Netmasha.

Every event is signed with a webhook secret that only this shop and Netmasha
know. A failed delivery is logged and never blocks the shop; Netmasha's hourly
readback reports anything that was missed.
""",
    "version": "17.0.1.1.0",
    "author": "Netmasha",
    "website": "https://netmasha.com",
    "category": "Sales/Sales",
    "license": "LGPL-3",
    "depends": ["website_sale", "sale", "account"],
    "data": [
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "netmasha_attribution/static/src/js/attribution.js",
        ],
    },
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
    "auto_install": False,
}
