# -*- coding: utf-8 -*-
"""Post-migration for payment_clover 19.0.4.4.

Backfills the new stored computed fields introduced for the
Reporting menu:

* clover.sale.line.unit_cost / cost_amount / margin_amount
* clover.sale.line.employee_id / partner_id / customer_display_name
  (related fields — Odoo auto-populates via ir.model.fields.related
  but recompute here guarantees indexed values for pre-existing rows
  under high row counts)
* clover.sale.cost_amount / net_amount / customer_display_name

Recomputes are wrapped in a single sudo() browse so ORM emits one
UPDATE per batch of records rather than a query per row.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Recompute the new stored computed fields on all existing rows."""
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})

    Line = env["clover.sale.line"].sudo()
    Sale = env["clover.sale"].sudo()

    line_ids = Line.search([])
    _logger.info(
        "payment_clover 19.0.4.4: recomputing cost fields on %s "
        "clover.sale.line rows",
        len(line_ids),
    )
    if line_ids:
        # Force recompute of the new stored fields.
        Line.invalidate_model([
            "unit_cost", "cost_amount", "margin_amount",
            "employee_id", "partner_id", "customer_display_name",
        ])
        line_ids.modified([
            "clover_item_id", "provider_id", "product_id",
            "unit_qty", "amount",
        ])
        # Trigger _compute for stored fields via recompute().
        env.flush_all()

    sale_ids = Sale.search([])
    _logger.info(
        "payment_clover 19.0.4.4: recomputing margin fields on %s "
        "clover.sale rows",
        len(sale_ids),
    )
    if sale_ids:
        Sale.invalidate_model([
            "cost_amount", "net_amount", "customer_display_name",
        ])
        sale_ids.modified([
            "line_ids", "total_amount", "partner_id", "customer_name",
        ])
        env.flush_all()

    _logger.info(
        "payment_clover 19.0.4.4: recompute complete.",
    )
