# -*- coding: utf-8 -*-
"""Post-migration for 19.0.4.1.

Backfills the newly added stored computed fields on every existing
clover.sale.line row:
* provider_id  (related to sale_id.provider_id)
* date         (related to sale_id.date)
* product_id   (compute via clover_item_id → clover.item.product_id)

Without this pass, historical lines would show product_id=NULL and
the product-level YTD rollups would only reflect data synced after
the upgrade. Recomputing once here means every existing sale-line
attributes to its product immediately.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    from odoo.api import Environment

    env = Environment(cr, SUPERUSER_ID, {})
    Line = env["clover.sale.line"].sudo()
    lines = Line.search([])
    if not lines:
        _logger.info("Clover 19.0.4.1: no clover.sale.line rows to "
                     "backfill; skipping.")
        return
    _logger.info(
        "Clover 19.0.4.1: recomputing product_id / provider_id / "
        "date on %d existing clover.sale.line rows…",
        len(lines),
    )
    # Invalidate cache for the stored computes and recompute.
    lines.invalidate_recordset(
        ["provider_id", "date", "product_id"])
    for chunk_start in range(0, len(lines), 500):
        chunk = lines[chunk_start:chunk_start + 500]
        chunk._compute_product_id()
        # Force ORM to flush the related-field materialization for
        # provider_id and date too.
        chunk.modified(["clover_item_id"])
        env.cr.commit()
    _logger.info("Clover 19.0.4.1: backfill complete.")
