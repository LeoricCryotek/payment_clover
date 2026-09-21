# -*- coding: utf-8 -*-
"""Mirror models for Clover POS sales, line items and tips.

These records are populated read-only from the Clover Platform API by
:meth:`payment.provider._clover_sync_sales` and used for reporting.
They intentionally do NOT post to Odoo accounting — the bookkeeper
reconciles Clover in QuickBooks Online. When the provider's
``clover_auto_create_sale_orders`` toggle is on, we ALSO create a
matching ``sale.order`` (silent — no confirmation email) so Odoo's
native sales reports include Clover revenue.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CloverSale(models.Model):
    """One row per Clover POS order or Ecommerce order."""
    _name = "clover.sale"
    _description = "Clover Sale (Mirror)"
    _order = "date desc, id desc"
    _rec_name = "clover_order_id"

    provider_id = fields.Many2one(
        "payment.provider",
        required=True,
        ondelete="cascade",
        index=True,
    )
    clover_order_id = fields.Char(
        string="Clover Order ID",
        required=True,
        index=True,
    )
    date = fields.Datetime(required=True, index=True)
    employee_id = fields.Many2one(
        "hr.employee",
        string="Cashier",
        help="Odoo employee linked to the Clover cashier for this "
             "order. Blank if the Clover cashier isn't yet linked "
             "to an hr.employee.",
        index=True,
    )
    clover_employee_id = fields.Many2one(
        "clover.employee",
        string="Clover Employee",
        ondelete="set null",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Contact",
        help="Existing Odoo contact matched by phone (SMS-receipt) "
             "or email, or auto-created if the provider setting is on.",
        index=True,
    )
    customer_name = fields.Char()
    customer_email = fields.Char()
    customer_phone = fields.Char()
    total_amount = fields.Float(
        digits=(12, 2),
        string="Total",
    )
    tip_amount = fields.Float(
        digits=(12, 2),
        compute="_compute_tip_amount",
        store=True,
    )
    currency_code = fields.Char(default="USD", size=3)
    state = fields.Char(help="Raw Clover order state (OPEN, PAID, "
                             "LOCKED, ...)")
    is_paid = fields.Boolean(default=False)
    line_ids = fields.One2many(
        "clover.sale.line", "sale_id",
        string="Line Items",
    )
    tip_ids = fields.One2many(
        "clover.tip.entry", "clover_sale_id",
        string="Tip Entries",
    )
    sale_order_id = fields.Many2one(
        "sale.order",
        string="Odoo Sale Order",
        ondelete="set null",
        help="Only set when clover_auto_create_sale_orders is on.",
    )
    last_synced = fields.Datetime(readonly=True)

    _unique_clover_order_per_provider = models.Constraint(
        "unique(provider_id, clover_order_id)",
        "Each Clover order can only be mirrored once per provider.",
    )

    @api.depends("tip_ids.amount", "tip_ids.is_refunded")
    def _compute_tip_amount(self):
        for sale in self:
            sale.tip_amount = sum(
                sale.tip_ids.filtered(lambda t: not t.is_refunded)
                .mapped("amount")
            )

    def _clover_ensure_sale_order(self):
        """Create a matching sale.order (silent) if not already present.

        Called from the sales sync when the provider setting
        ``clover_auto_create_sale_orders`` is on. Bypasses
        ``action_confirm()`` — which is where Odoo's order-confirmation
        email lives — and sets ``state='sale'`` directly. Uses the
        silent context so no follower emails, tracking entries, or
        portal invites fire.

        Skips gracefully when:
        * the ``sale`` module isn't installed
        * the Clover sale has no matched partner
        * any line doesn't have a Clover item → Odoo product link
          (sale.order.line requires product_id; rather than invent a
          placeholder we skip the sale.order creation and log why so
          you can link the items in Clover Items and retry)
        """
        if "sale.order" not in self.env:
            _logger.info("Clover: 'sale' module not installed — "
                         "auto-create sale.order skipped.")
            return
        for sale in self:
            if sale.sale_order_id:
                continue
            if not sale.partner_id:
                # No contact to attach the sale.order to — skip.
                # The clover.sale mirror still exists for reporting.
                continue
            silent = sale.provider_id._clover_silent_context()
            SO = self.env["sale.order"].sudo().with_context(**silent)
            order_lines = []
            missing_product = False
            for line in sale.line_ids:
                product = line.clover_item_ref().product_id
                if not product:
                    missing_product = True
                    break
                order_lines.append((0, 0, {
                    "name": line.description or _("Clover line"),
                    "product_id": product.id,
                    "product_uom_qty": line.unit_qty or 1,
                    "price_unit": line.amount,
                }))
            if missing_product or not order_lines:
                _logger.info(
                    "Clover: skipping sale.order for %s — one or "
                    "more lines lack an Odoo product link.",
                    sale.clover_order_id,
                )
                continue
            so_vals = {
                "partner_id": sale.partner_id.id,
                "date_order": sale.date,
                "origin": f"Clover {sale.clover_order_id}",
                "order_line": order_lines,
                "note": _("Auto-created from Clover POS sync. "
                          "Reconciliation happens outside Odoo."),
            }
            new_so = SO.create(so_vals)
            # Bypass action_confirm() on purpose — that's where the
            # order-confirmation email lives. Set state directly.
            # No pickings, no invoices, no emails; the sale.order is
            # a reporting record only. If the user later wants to
            # invoice a Clover sale, they can do it manually and the
            # normal email flow will fire as expected.
            new_so.write({"state": "sale"})
            sale.sale_order_id = new_so.id
            _logger.info(
                "Clover: created silent sale.order %s for clover.sale %s",
                new_so.id, sale.clover_order_id,
            )

    def action_open_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            raise UserError(_("No Odoo sale.order linked to this "
                              "Clover sale."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id": self.sale_order_id.id,
            "view_mode": "form",
        }


class CloverSaleLine(models.Model):
    """One row per line item on a Clover order."""
    _name = "clover.sale.line"
    _description = "Clover Sale Line"
    _order = "id"

    sale_id = fields.Many2one(
        "clover.sale",
        required=True,
        ondelete="cascade",
        index=True,
    )
    clover_line_id = fields.Char(string="Clover Line ID")
    clover_item_id = fields.Char(
        string="Clover Item ID",
        index=True,
        help="Populated from Clover's item reference on the line "
             "item, when Clover was able to attach one. Use this to "
             "look up the matching clover.item / product.product.",
    )
    description = fields.Char()
    unit_qty = fields.Float(default=1.0)
    amount = fields.Float(
        digits=(12, 2),
        help="Per-line total in the sale currency.",
    )
    note = fields.Char()
    provider_id = fields.Many2one(
        related="sale_id.provider_id",
        store=True,
        index=True,
        help="Denormalized from sale_id so product_id compute stays "
             "in sync with provider (needed for the clover.item "
             "lookup domain).",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Odoo Product",
        compute="_compute_product_id",
        store=True,
        index=True,
        help="Auto-resolved via clover_item_id → clover.item.product_id. "
             "Set only when the Clover item has been linked to a "
             "product (do the linking on Configuration → Clover "
             "Items). Enables sales-by-product rollups on the "
             "product form.",
    )
    date = fields.Datetime(
        related="sale_id.date",
        store=True,
        index=True,
        help="Denormalized from sale_id.date so per-product YTD "
             "rollups can filter on line-level dates efficiently.",
    )

    @api.depends("clover_item_id", "provider_id")
    def _compute_product_id(self):
        Item = self.env["clover.item"].sudo()
        for line in self:
            if not line.clover_item_id or not line.provider_id:
                line.product_id = False
                continue
            item = Item.search([
                ("provider_id", "=", line.provider_id.id),
                ("clover_item_id", "=", line.clover_item_id),
            ], limit=1)
            line.product_id = item.product_id.id if item else False

    def clover_item_ref(self):
        """Return the matching clover.item record for this line, if any."""
        self.ensure_one()
        if not self.clover_item_id:
            return self.env["clover.item"]
        return self.env["clover.item"].sudo().search([
            ("provider_id", "=", self.sale_id.provider_id.id),
            ("clover_item_id", "=", self.clover_item_id),
        ], limit=1)


class CloverTipEntry(models.Model):
    """One row per Clover payment that carried a tip.

    Feeds the Tips-by-Employee report and the hr.employee
    ``x_clover_tips_this_period`` computed field. A separate row per
    payment (not per order) so partial-payment scenarios are captured
    accurately.
    """
    _name = "clover.tip.entry"
    _description = "Clover Tip Entry"
    _order = "date desc, id desc"

    provider_id = fields.Many2one(
        "payment.provider",
        required=True,
        ondelete="cascade",
        index=True,
    )
    clover_payment_id = fields.Char(
        string="Clover Payment ID",
        required=True,
        index=True,
    )
    clover_sale_id = fields.Many2one(
        "clover.sale",
        string="Clover Sale",
        ondelete="set null",
        index=True,
    )
    date = fields.Datetime(required=True, index=True)
    amount = fields.Float(digits=(12, 2), string="Tip Amount")
    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        index=True,
        help="Odoo employee linked to the Clover cashier who took "
             "the payment. Blank if the Clover cashier isn't yet "
             "linked.",
    )
    clover_employee_id = fields.Many2one(
        "clover.employee",
        string="Clover Employee",
        ondelete="set null",
    )
    is_event_gratuity = fields.Boolean(
        string="Event Gratuity",
        default=False,
        help="When True, this tip is a pooled event gratuity and is "
             "EXCLUDED from personal-employee tip reports. Flip via "
             "the event-gratuity module (or set manually).",
    )
    is_unclaimed = fields.Boolean(
        string="Unclaimed",
        default=False,
        help="TRUE when the employee's department is not "
             "tip-eligible (x_clover_tips_eligible=False on the "
             "department — e.g. Volunteers). The tip is recorded "
             "against the employee for audit but does NOT count "
             "toward their personal Clover tip total. Managers can "
             "review unclaimed tips separately for allocation.",
    )
    is_refunded = fields.Boolean(default=False)
    last_synced = fields.Datetime(readonly=True)

    _unique_clover_payment = models.Constraint(
        "unique(provider_id, clover_payment_id)",
        "Each Clover payment can only produce one tip entry.",
    )
