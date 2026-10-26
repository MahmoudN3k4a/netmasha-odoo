{
    "name": "Netmasha Attribution",
    "summary": "Connect your Odoo shop to Netmasha with one click: click-id attribution, real-time orders, cancellations and refunds, and single-use discount codes for Netmasha points.",
    "description": """
Netmasha Attribution connects this Odoo shop to your Netmasha account. In
Settings > Netmasha press "Connect to Netmasha", sign in and approve: there is
nothing to copy or paste.

- It captures the Netmasha click id a shopper carries from the Netmasha mall
  and keeps it on the sale order.
- When a customer invoice for such an order becomes paid, the addon sends a
  signed event to Netmasha.
- When such an order is cancelled, the addon sends a signed cancellation
  event to Netmasha.
- When a credit note is posted for such an order, the addon sends a signed
  refund event to Netmasha.
- When a shopper spends Netmasha points at this shop, Netmasha asks the addon
  (signed request) to create a single-use Netmasha discount code with Odoo's
  Loyalty module, and the paid-order event lists the Netmasha code that was used.
  This needs Odoo Loyalty (sale_loyalty), which is installed with the addon.

Every event is signed with a webhook secret that only this shop and Netmasha
know. A failed delivery is logged and never blocks the shop; Netmasha's hourly
readback reports anything that was missed.
""",
    "version": "17.0.1.4.0",
    "author": "Netmasha",
    "website": "https://netmasha.com",
    "category": "Sales/Sales",
    "license": "LGPL-3",
    "depends": ["website_sale", "sale", "account", "sale_loyalty"],
    "data": [
        "views/res_config_settings_views.xml",
        "data/ir_cron.xml",
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
