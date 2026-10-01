# -*- coding: utf-8 -*-
"""Bulk-reassign Clover tips to an hr.employee.

Opened from the Clover Tips list via the Actions dropdown — the
admin ticks the orphan rows (or any mix of rows) they want to
reassign, picks the receiving hr.employee, and clicks Apply.

Writes employee_id on every selected tip and recomputes
is_unclaimed against the receiving employee's department
tip-eligibility.
"""
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CloverTipReassignWizard(models.TransientModel):
    _name = "clover.tip.reassign.wizard"
    _description = "Reassign Clover Tips to Employee"

    tip_ids = fields.Many2many(
        "clover.tip.entry",
        string="Tips to Reassign",
        required=True,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Assign To",
        required=True,
        help="Every selected tip will have employee_id set to this "
             "hr.employee and is_unclaimed recomputed against "
             "their department tip-eligibility.",
    )
    recompute_unclaimed = fields.Boolean(
        string="Recompute Unclaimed Flag",
        default=True,
        help="When on, each tip's is_unclaimed field is reset "
             "based on the new employee's department — tip-eligible "
             "department → False, non-eligible (e.g. Volunteers) "
             "→ True. Turn off to leave is_unclaimed untouched.",
    )
    tip_count = fields.Integer(
        compute="_compute_tip_count",
        string="Tip Count",
    )
    tip_total = fields.Float(
        compute="_compute_tip_count",
        string="Total $",
        digits=(12, 2),
    )

    def _compute_tip_count(self):
        for wiz in self:
            wiz.tip_count = len(wiz.tip_ids)
            wiz.tip_total = sum(wiz.tip_ids.mapped("amount"))

    def action_apply(self):
        self.ensure_one()
        if not self.tip_ids:
            raise UserError(_("No tips were selected."))
        dept = self.employee_id.department_id
        should_be_unclaimed = bool(
            dept and not dept.x_clover_tips_eligible
        )
        for tip in self.tip_ids:
            vals = {"employee_id": self.employee_id.id}
            if (self.recompute_unclaimed
                    and tip.is_unclaimed != should_be_unclaimed):
                vals["is_unclaimed"] = should_be_unclaimed
            tip.write(vals)

        _logger.info(
            "payment_clover: bulk-reassigned %s tips (total $%.2f) "
            "to hr.employee %s (%s)",
            len(self.tip_ids), self.tip_total,
            self.employee_id.id, self.employee_id.name,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Tips Reassigned"),
                "message": _(
                    "%(n)s tips (total $%(t).2f) assigned to %(name)s."
                ) % {
                    "n": len(self.tip_ids),
                    "t": self.tip_total,
                    "name": self.employee_id.name,
                },
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
