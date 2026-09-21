# -*- coding: utf-8 -*-
"""Post-migration for payment_clover 19.0.4.4.

Backfills the new stored/related fields introduced for the
Reporting menu:

* clover.sale.line.unit_cost / cost_amount / margin_amount
  (stored computes — recomputed via .modified() triggers).
* clover.sale.line.employee_id / partner_id / customer_display_name
  (stored *related* fields — Odoo does not backfill related-store
  columns on module upgrade if none of the source fields changed
  value, so we do it in SQL for speed and reliability).
* clover.sale.cost_amount / net_amount / customer_display_name
  (stored computes on the parent).

Direct SQL is used for the related-store columns because it is
several orders of magnitude faster than an ORM per-record recompute
on a table with hundreds of thousands of rows, and it sidesteps the
version-conflict-on-upgrade edge case where the ORM has the new
field in the registry but the row in memory pre-dates the column.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Backfill new stored fields on existing rows."""
    # ---- 1. Related-store columns on clover.sale.line via SQL. ----
    _logger.info(
        "payment_clover 19.0.4.4: backfilling related-store fields "
        "on clover_sale_line via SQL.",
    )
    cr.execute("""
        UPDATE clover_sale_line l
           SET employee_id = s.employee_id,
               partner_id  = s.partner_id
          FROM clover_sale s
         WHERE l.sale_id = s.id
           AND (l.employee_id IS DISTINCT FROM s.employee_id
                OR l.partner_id IS DISTINCT FROM s.partner_id)
    """)
    _logger.info(
        "payment_clover 19.0.4.4: backfilled employee_id/partner_id "
        "on %s clover_sale_line rows.",
        cr.rowcount,
    )

    # ---- 2. Recompute stored computes via ORM. ----
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})

    Sale = env["clover.sale"].sudo()
    Line = env["clover.sale.line"].sudo()

    # customer_display_name is a compute on clover.sale — recompute
    # for every parent, then the related-store copy on the line
    # cascades in the flush.
    sales = Sale.search([])
    _logger.info(
        "payment_clover 19.0.4.4: recomputing customer_display_name / "
        "cost_amount / net_amount on %s clover.sale rows.",
        len(sales),
    )
    if sales:
        sales.invalidate_recordset(
            fnames=["customer_display_name", "cost_amount",
                    "net_amount"])
        sales._compute_customer_display_name()
        sales.modified(["partner_id", "customer_name"])
        env.flush_all()

    # Line-level cost/margin recompute (triggers off clover_item_id
    # + provider_id + product_id + unit_qty + amount).
    lines = Line.search([])
    _logger.info(
        "payment_clover 19.0.4.4: recomputing unit_cost / cost_amount / "
        "margin_amount on %s clover.sale.line rows.",
        len(lines),
    )
    if lines:
        lines.invalidate_recordset(
            fnames=["unit_cost", "cost_amount", "margin_amount",
                    "customer_display_name"])
        lines._compute_line_cost()
        env.flush_all()

    # Roll the line-level costs back UP to the parent sale.
    if sales:
        sales.invalidate_recordset(fnames=["cost_amount", "net_amount"])
        sales.modified(["line_ids"])
        env.flush_all()

    _logger.info("payment_clover 19.0.4.4: recompute complete.")
