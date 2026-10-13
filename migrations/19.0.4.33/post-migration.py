# -*- coding: utf-8 -*-
"""Backfill unit_qty on historical clover.sale.line rows.

Clover's Ecommerce API only sends `unitQty` for items billed per
unit of measure (per-ounce pours, per-pound deli, etc.). For
fixed-item sales — one bottle of Antlers Lotus, one burger, one
soda — Clover omits the field entirely.

Before the fix in version 19.0.4.1-ish, the sync wrote
`unit_qty = 0` in those cases, which made the per-product unit
rollup on product.template.x_clover_units_sold_ytd read 0 units
for 19 lines totalling $57 — nonsensical.

The fix defaulted missing `unitQty` to 1.0 going forward, but
rows synced BEFORE the fix still have unit_qty = 0 in the DB.
This migration repairs them in one SQL UPDATE: any row with
unit_qty = 0 AND amount > 0 gets unit_qty = 1.0. (A sold line
with revenue clearly represents at least one unit sold; the
alternative is a bogus 'free' interpretation that no bar makes.)

Also recomputes cost_amount + margin_amount on the touched rows
via the ORM so the downstream stored computed fields update too.
Safe to re-run — only hits rows that need it.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE clover_sale_line
           SET unit_qty = 1.0
         WHERE (unit_qty IS NULL OR unit_qty = 0)
           AND amount IS NOT NULL
           AND amount > 0
    """)
    touched = cr.rowcount
    _logger.info(
        "payment_clover 19.0.4.33: backfilled unit_qty = 1.0 on "
        "%s historical sale-line rows (unit_qty was 0 but amount > 0).",
        touched,
    )

    if not touched:
        return

    # Recompute the stored computed fields that depend on unit_qty
    # (cost_amount, margin_amount). Done via the ORM so dependent
    # rollups on product.template and clover.sale fire correctly.
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    Line = env["clover.sale.line"].sudo()
    lines = Line.search([
        ("unit_qty", "=", 1.0),
        ("cost_amount", "=", 0.0),
        ("amount", ">", 0.0),
    ])
    if lines:
        lines.invalidate_recordset(
            ["cost_amount", "margin_amount", "unit_cost"])
        lines._compute_line_cost()
        env.flush_all()
        _logger.info(
            "payment_clover 19.0.4.33: recomputed cost_amount / "
            "margin_amount on %s rows.", len(lines),
        )
