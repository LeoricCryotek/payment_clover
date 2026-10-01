# -*- coding: utf-8 -*-
"""Remap-with-cutoff wizard for clover.employee.

Lets an admin re-attribute a Clover cashier to a different Odoo
employee from a specific moment forward, instead of the all-or-nothing
backfill the regular write() override does.

Use-case: Jane leaves the lounge and Jon takes over her Clover login
on Sep 15 at 2pm. Historically all of her sales were attributed to
her hr.employee record. Going forward, every sale on that Clover
login belongs to Jon. The admin opens the wizard, picks Jon, sets
cutoff = Sep 15 14:00, hits Apply:

    * All clover.sale / clover.tip.entry rows with date >= Sep 15 14:00
      → re-attributed to Jon.
    * All earlier rows → unchanged (stay with Jane).
    * The clover.employee.employee_id field is updated to Jon so
      every future sync lands on him by default.

If the admin ticks "Transfer every historical record" the cutoff
field is ignored and the wizard behaves like the plain write hook
(full retroactive move).

The wizard writes with a special context key so clover.employee's
write() override skips its own automatic full-history backfill —
we do the backfill ourselves here with the chosen cutoff.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CloverEmployeeRemapWizard(models.TransientModel):
    _name = "clover.employee.remap.wizard"
    _description = "Clover Employee Remap Wizard"

    clover_employee_id = fields.Many2one(
        "clover.employee",
        required=True,
        ondelete="cascade",
        string="Clover Cashier",
    )
    clover_cashier_name = fields.Char(
        related="clover_employee_id.name",
        readonly=True,
    )
    current_employee_id = fields.Many2one(
        "hr.employee",
        related="clover_employee_id.employee_id",
        string="Currently Mapped To",
        readonly=True,
    )
    new_employee_id = fields.Many2one(
        "hr.employee",
        string="New Odoo Employee",
        required=True,
        help="The hr.employee that should receive this Clover "
             "cashier's sales + tips from the effective date onward.",
    )
    effective_from = fields.Datetime(
        string="Effective From",
        required=True,
        default=fields.Datetime.now,
        help="Clover sales and tips with a timestamp on or after "
             "this moment will be re-attributed to the new "
             "employee. Earlier records stay with the previous "
             "mapping. Time is interpreted in UTC — pick a slightly "
             "earlier moment if you're unsure.",
    )
    transfer_all_history = fields.Boolean(
        string="Transfer Every Historical Record",
        default=False,
        help="When on, every historical sale and tip for this "
             "Clover cashier is moved to the new employee, "
             "regardless of the effective date. Equivalent to the "
             "default behaviour when you edit the Odoo Employee "
             "field directly on the Clover Employee form.",
    )
    sale_count_moving = fields.Integer(
        string="Sales to Move",
        compute="_compute_counts",
    )
    tip_count_moving = fields.Integer(
        string="Tips to Move",
        compute="_compute_counts",
    )
    sale_count_staying = fields.Integer(
        string="Sales Staying with Previous",
        compute="_compute_counts",
    )
    tip_count_staying = fields.Integer(
        string="Tips Staying with Previous",
        compute="_compute_counts",
    )

    @api.depends(
        "clover_employee_id", "effective_from",
        "transfer_all_history",
    )
    def _compute_counts(self):
        """Preview counts so admins see what the apply will do."""
        Sale = self.env["clover.sale"].sudo()
        Tip = self.env["clover.tip.entry"].sudo()
        for wiz in self:
            if not wiz.clover_employee_id:
                wiz.sale_count_moving = 0
                wiz.tip_count_moving = 0
                wiz.sale_count_staying = 0
                wiz.tip_count_staying = 0
                continue
            base = [("clover_employee_id", "=",
                     wiz.clover_employee_id.id)]
            if wiz.transfer_all_history:
                wiz.sale_count_moving = Sale.search_count(base)
                wiz.tip_count_moving = Tip.search_count(base)
                wiz.sale_count_staying = 0
                wiz.tip_count_staying = 0
            elif wiz.effective_from:
                moving = [("date", ">=", wiz.effective_from)]
                staying = [("date", "<", wiz.effective_from)]
                wiz.sale_count_moving = Sale.search_count(
                    base + moving)
                wiz.tip_count_moving = Tip.search_count(
                    base + moving)
                wiz.sale_count_staying = Sale.search_count(
                    base + staying)
                wiz.tip_count_staying = Tip.search_count(
                    base + staying)
            else:
                wiz.sale_count_moving = 0
                wiz.tip_count_moving = 0
                wiz.sale_count_staying = 0
                wiz.tip_count_staying = 0

    # ---------------------------------------------------------------
    # Actions
    # ---------------------------------------------------------------
    def action_apply(self):
        self.ensure_one()
        if not self.new_employee_id:
            raise UserError(_("Pick an Odoo employee first."))

        Sale = self.env["clover.sale"].sudo()
        Tip = self.env["clover.tip.entry"].sudo()
        clover_emp = self.clover_employee_id

        # Figure out which rows move. Base scope is "every row
        # attributed to this clover.employee"; the cutoff filters
        # it down unless the admin asked for a full transfer.
        base_domain = [("clover_employee_id", "=", clover_emp.id)]
        if self.transfer_all_history:
            move_domain = base_domain
            _logger.info(
                "payment_clover: remap wizard moving ALL history "
                "for clover.employee %s → hr.employee %s",
                clover_emp.name, self.new_employee_id.name,
            )
        else:
            move_domain = base_domain + [
                ("date", ">=", self.effective_from),
            ]
            _logger.info(
                "payment_clover: remap wizard moving history "
                "from %s onward for clover.employee %s "
                "→ hr.employee %s",
                self.effective_from, clover_emp.name,
                self.new_employee_id.name,
            )

        # Move sales
        sales = Sale.search(move_domain)
        if sales:
            sales.write({"employee_id": self.new_employee_id.id})

        # Move tips + recompute is_unclaimed against the new
        # department's tip-eligibility (volunteer dept →
        # is_unclaimed=True).
        tips = Tip.search(move_domain)
        new_dept = self.new_employee_id.department_id
        should_be_unclaimed = bool(
            new_dept and not new_dept.x_clover_tips_eligible
        )
        for t in tips:
            vals = {"employee_id": self.new_employee_id.id}
            if t.is_unclaimed != should_be_unclaimed:
                vals["is_unclaimed"] = should_be_unclaimed
            t.write(vals)

        # Update the clover.employee's default-mapping employee_id
        # to the new one, but suppress the auto-backfill in its
        # write() override — we already did the backfill, with the
        # chosen cutoff. The context key is what the override
        # checks to short-circuit.
        clover_emp.with_context(
            clover_skip_backfill=True,
        ).write({
            "employee_id": self.new_employee_id.id,
        })

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Clover Mapping Updated"),
                "message": _(
                    "%(sales)s sales and %(tips)s tips moved to %(name)s."
                ) % {
                    "sales": len(sales),
                    "tips": len(tips),
                    "name": self.new_employee_id.name,
                },
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
