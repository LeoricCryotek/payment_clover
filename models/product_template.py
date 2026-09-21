# -*- coding: utf-8 -*-
"""Product-level Clover sales rollups.

Adds YTD sales/units/revenue counters and a lookup of every
clover.sale.line that references this product, so users can open any
product and see how it's performing at the Clover POS terminal.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = "product.template"

    x_clover_sale_line_ids = fields.One2many(
        "clover.sale.line",
        "product_id",
        compute="_compute_x_clover_sale_line_ids",
        string="Clover POS Sale Lines",
    )
    x_clover_units_sold_ytd = fields.Float(
        string="Clover Units Sold (YTD)",
        compute="_compute_x_clover_stats",
        digits=(12, 2),
        help="Sum of unit_qty across all clover.sale.line rows for "
             "this product from Jan 1 of the current year to now.",
    )
    x_clover_revenue_ytd = fields.Float(
        string="Clover Revenue (YTD, USD)",
        compute="_compute_x_clover_stats",
        digits=(12, 2),
        help="Sum of per-line amounts for this product from Jan 1 "
             "of the current year to now. Not gross of tips, taxes, "
             "or Clover fees — this is the item-line total as Clover "
             "reports it.",
    )
    x_clover_sale_count_ytd = fields.Integer(
        string="Clover Orders Sold In (YTD)",
        compute="_compute_x_clover_stats",
        help="Number of distinct Clover orders this product "
             "appeared on since Jan 1 of the current year.",
    )
    x_clover_last_sold_at = fields.Datetime(
        string="Last Sold in Clover",
        compute="_compute_x_clover_stats",
    )

    def _compute_x_clover_sale_line_ids(self):
        """Resolve the One2many through product.product → template.

        clover.sale.line.product_id points at product.product (which
        is 1:1 with a template when no variants). We expose it as a
        template field so the rollups sit on the master product form
        without users needing to think about variants.
        """
        Line = self.env["clover.sale.line"].sudo()
        for tmpl in self:
            variant_ids = tmpl.product_variant_ids.ids
            if not variant_ids:
                tmpl.x_clover_sale_line_ids = Line
                continue
            tmpl.x_clover_sale_line_ids = Line.search([
                ("product_id", "in", variant_ids),
            ])

    @api.depends("x_clover_sale_line_ids",
                 "x_clover_sale_line_ids.unit_qty",
                 "x_clover_sale_line_ids.amount",
                 "x_clover_sale_line_ids.date")
    def _compute_x_clover_stats(self):
        ytd_start = fields.Datetime.now().replace(
            month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        for tmpl in self:
            lines = tmpl.x_clover_sale_line_ids.filtered(
                lambda l: l.date and l.date >= ytd_start)
            tmpl.x_clover_units_sold_ytd = sum(lines.mapped("unit_qty"))
            tmpl.x_clover_revenue_ytd = sum(lines.mapped("amount"))
            # Count distinct clover.sale orders these lines are on.
            sales = lines.mapped("sale_id")
            tmpl.x_clover_sale_count_ytd = len(sales)
            all_lines = tmpl.x_clover_sale_line_ids
            last_sold = max(
                (l.date for l in all_lines if l.date),
                default=False,
            )
            tmpl.x_clover_last_sold_at = last_sold
