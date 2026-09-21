# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class CloverItem(models.Model):
    """Map a Clover inventory item to an Odoo product.

    Stores the Clover item ID and metadata so the terminal can display
    a product picker, and charges can include item-level detail.
    """
    _name = "clover.item"
    _description = "Clover Inventory Item"
    _order = "name"
    _rec_name = "name"

    provider_id = fields.Many2one(
        "payment.provider",
        required=True,
        ondelete="cascade",
        index=True,
    )
    clover_item_id = fields.Char(
        string="Clover Item ID",
        required=True,
        index=True,
    )
    name = fields.Char(required=True)
    price = fields.Float(
        digits=(10, 2),
        help="Price in dollars (Clover stores cents; converted on sync).",
    )
    price_type = fields.Selection(
        [
            ("FIXED", "Fixed"),
            ("VARIABLE", "Variable"),
            ("PER_UNIT", "Per Unit"),
        ],
        default="FIXED",
    )
    sku = fields.Char(string="SKU")
    category_name = fields.Char(string="Category")
    hidden = fields.Boolean(default=False)
    active = fields.Boolean(default=True)
    cost = fields.Float(
        digits=(10, 2),
        string="Cost",
        help="Item cost pulled from Clover (Platform API 'cost' "
             "field, converted to dollars). Written to the linked "
             "product's standard_price when the provider's "
             "'Sync Item Costs to Products' setting is on.",
    )
    exclude_from_employee_tips = fields.Boolean(
        string="Exclude Tips from Employee Reports",
        default=False,
        help="When True, tips collected on Clover orders that "
             "contain this item are treated as event/pool gratuity "
             "and NOT counted toward individual employee tip totals. "
             "Set by the event-gratuity module or manually.",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Odoo Product",
        help="Linked Odoo product. Auto-created on first sync if blank.",
    )
    last_synced = fields.Datetime(readonly=True)

    _unique_clover_item_per_provider = models.Constraint(
        "unique(provider_id, clover_item_id)",
        "Each Clover item can only be linked once per provider.",
    )
