# -*- coding: utf-8 -*-
{
    "name": "Payment Provider: Clover",
    "version": "19.0.1.1",
    "category": "Accounting/Payment Providers",
    "summary": "Accept payments via Clover (charges, refunds, auth/capture).",
    "description": """
Payment Provider: Clover
========================

Integrate Clover's Ecommerce API as an Odoo payment provider.

Features
--------
* Online card payments via Clover's hosted iframe (PCI SAQ-A compliant)
* Authorization with manual capture
* Full and partial refunds
* Webhook support for asynchronous payment notifications
* Standalone payment terminal screen for staff
* Sandbox and production environments

Setup
-----
1. Create a Clover developer account and a merchant.
2. Obtain your API key (Bearer token) and PAKMS public tokenizer key.
3. Configure the Clover provider in Odoo Payment Providers.
4. Enable the provider and process payments.

Dependencies
------------
payment
""",
    "author": "Danny Santiago",
    "website": "https://dannysantiago.info",
    "license": "LGPL-3",
    "depends": ["payment"],
    "data": [
        "views/payment_provider_views.xml",
        "views/payment_clover_templates.xml",
        "views/payment_terminal_views.xml",
        "data/payment_provider_data.xml",
        "data/payment_method_data.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "payment_clover/static/src/js/payment_form.js",
        ],
        "web.assets_backend": [
            "payment_clover/static/src/js/payment_terminal.js",
            "payment_clover/static/src/xml/payment_terminal.xml",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
}
