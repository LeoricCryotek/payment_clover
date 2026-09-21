# -*- coding: utf-8 -*-
{
    "name": "Payment Provider: Clover",
    "version": "19.0.4.6",
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
    # Odoo core modules only.
    # 'hr'   — 19.0.2.0: Clover employee sync + tips reporting
    #          (clover.employee links to hr.employee).
    # 'sale' — 19.0.2.3: clover.sale.sale_order_id is a Many2one
    #          to sale.order (populated only when the provider's
    #          "Auto-Create sale.order Records from Clover Sales"
    #          toggle is ON — which itself defaults OFF). The Many2one
    #          still needs sale.order registered at boot, so 'sale'
    #          must be in depends even when the toggle is off.
    # 'board' — 19.0.4.5: Clover Dashboard (board.board form embeds
    #           our graph/pivot actions as a KPI overview page).
    "depends": ["payment", "product", "hr", "sale", "board"],
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
        # 6. Sync engine views + menus (added in 19.0.2.0).
        #    Depends on menu_clover_root from payment_terminal_views.
        "views/clover_sync_views.xml",
        # Reporting suite — loaded after clover_sync_views so its
        # menu items can nest under menu_clover_root and reference
        # clover_sale_view_search / clover_tip_view_search.
        "views/clover_reporting_views.xml",
        # Dashboard — board.board form combining the reporting
        # actions. Must load AFTER clover_reporting_views.xml
        # (references action_clover_report_* records) and AFTER
        # payment_terminal_views.xml (references menu_clover_root).
        "views/clover_dashboard_views.xml",
        "views/hr_department_views.xml",
        "views/hr_employee_views.xml",
        "views/product_template_views.xml",
        # Preview wizard menu references menu_clover_sales_root from
        # clover_sync_views.xml, so it must load AFTER that file.
        "wizard/clover_sync_preview_wizard_views.xml",
        # 7. Data records (provider record references inline_form view).
        "data/payment_provider_data.xml",
        "data/payment_method_data.xml",
        "data/clover_cron.xml",
        # Holding-pen department for Clover-auto-created hr.employees.
        # Loaded after hr_department_views (needs
        # x_clover_tips_eligible field defined).
        "data/hr_department_data.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "payment_clover/static/src/js/payment_form.js",
        ],
        "web.assets_backend": [
            "payment_clover/static/src/js/payment_terminal.js",
            "payment_clover/static/src/xml/payment_terminal.xml",
            # 19.0.4.6: patches the graph view's Chart.js config so
            # axis tick labels + legend + axis titles render in a
            # dark body colour instead of Odoo's default pale grey.
            "payment_clover/static/src/js/graph_dark_ticks.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
}
