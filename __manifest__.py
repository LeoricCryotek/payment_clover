# -*- coding: utf-8 -*-
{
    "name": "Payment Provider: Clover",
    "version": "19.0.1.12",
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
Depends only on Odoo core modules: ``payment`` (provider/transaction
framework) and ``product`` (records the terminal item picker syncs
into). Security groups are defined inside this module so no other
custom modules are required.

Access control
--------------
This module installs a "Clover Terminal" permission category with two
levels visible on the user form:

* **User** — process payments through the staff terminal.
* **Administrator** — also see configuration (gear in the terminal
  and a Configuration menu) and run the transaction report wizard.
""",
    "author": "Danny Santiago",
    "website": "https://dannysantiago.info",
    "license": "LGPL-3",
    # Truly standalone — only Odoo core modules.
    "depends": ["payment", "product"],
    "data": [
        # 1. Security groups must load BEFORE the access CSV references them.
        "security/payment_clover_groups.xml",
        "security/ir.model.access.csv",
        # 2. Inline form template — referenced by payment_provider_data.xml.
        "views/payment_clover_templates.xml",
        # 3. Provider + transaction views.
        "views/payment_provider_views.xml",
        "views/clover_transaction_views.xml",
        # 4. Wizard + report (must precede payment_terminal_views.xml because
        # its menuitem references action_clover_transaction_report_wizard).
        "wizard/clover_transaction_report_wizard_views.xml",
        "report/clover_transaction_report.xml",
        # 5. Terminal menus + client action (references actions above).
        "views/payment_terminal_views.xml",
        # 6. Data records (provider record references inline_form view).
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
