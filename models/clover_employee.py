# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class CloverEmployee(models.Model):
    """Mirror of a Clover Merchant employee, linked to hr.employee.

    Populated by :meth:`payment.provider._clover_sync_employees`. One
    row per Clover employee per provider. Automatic linking to
    hr.employee is attempted by email → phone → name; unlinked rows
    remain visible in the list view so an admin can drag them onto
    the right hr.employee record manually.

    The Clover ``Float`` profile (a default the platform creates that
    isn't a real person) is captured with ``is_float=True`` so tip
    reports can exclude it.
    """
    _name = "clover.employee"
    _description = "Clover Employee"
    _order = "name"
    _rec_name = "name"

    provider_id = fields.Many2one(
        "payment.provider",
        required=True,
        ondelete="cascade",
        index=True,
    )
    clover_employee_id = fields.Char(
        string="Clover Employee ID",
        required=True,
        index=True,
    )
    name = fields.Char(required=True)
    nickname = fields.Char()
    email = fields.Char()
    role = fields.Char(help="Clover-side role (ADMIN, MANAGER, EMPLOYEE, "
                            "FLOAT, etc.)")
    is_float = fields.Boolean(
        string="Float Profile",
        default=False,
        help="Marks Clover's default Float profile so its tips can "
             "be excluded from employee tip reports (Float is not a "
             "real person).",
    )
    exclude_from_reports = fields.Boolean(
        string="Exclude from Employee Tip Reports",
        default=False,
        help="When ON, tips this Clover account collects are "
             "flagged as event/pooled gratuity and stay OUT of "
             "personal-employee tip totals. Automatically set for "
             "the Clover Float profile; admins can also flip it ON "
             "for contractor, one-off, or shared-terminal accounts "
             "that shouldn't count toward any individual's tips.",
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Odoo Employee",
        ondelete="set null",
        help="hr.employee this Clover cashier maps to. Auto-linked "
             "by the nightly sync when email / phone / name match; "
             "set manually otherwise. Blank + not-flagged-Float + "
             "not exclude_from_reports means tips fall into the "
             "'unassigned' bucket on the tip report — a signal to "
             "admins that this Clover account needs a decision.",
    )
    active = fields.Boolean(default=True)
    last_synced = fields.Datetime(readonly=True)

    _unique_clover_emp_per_provider = models.Constraint(
        "unique(provider_id, clover_employee_id)",
        "Each Clover employee can only be mirrored once per provider.",
    )


class HrDepartment(models.Model):
    """Department-level tip eligibility flag.

    When OFF, tips collected in Clover by any employee in this
    department are recorded as ``is_unclaimed=True`` on the
    tip entry — they stay OUT of the employee's personal tip totals
    and OUT of pooled event gratuity. Managers can allocate them
    separately or leave them in the house pool.

    Use for volunteer departments, salaried managers on the floor,
    or any group that runs the terminal but doesn't personally
    receive tips.
    """
    _inherit = "hr.department"

    x_clover_tips_eligible = fields.Boolean(
        string="Employees Can Collect Clover Tips",
        default=True,
        help="ON (default): tips this department's employees collect "
             "count as their personal Clover tips. OFF: tips are "
             "recorded as UNCLAIMED — separate bucket from personal "
             "and from event gratuity. Turn OFF for volunteer, "
             "salaried-manager, or shared-role departments.",
    )


class HrEmployee(models.Model):
    """Add a computed convenience field for Clover tips per period.

    We keep the aggregation OUTSIDE hr.attendance / timesheet on
    purpose — timekeeping approval lives in another module and this
    field is for read-side reporting only, not for approval workflow.
    """
    _inherit = "hr.employee"

    x_clover_employee_ids = fields.One2many(
        "clover.employee", "employee_id",
        string="Clover Employee Links",
    )
    x_clover_tips_this_period = fields.Float(
        string="Clover Tips (This Month, USD)",
        compute="_compute_x_clover_tips_this_period",
        digits=(12, 2),
        help="Sum of tips collected in Clover for this employee "
             "since the start of the current calendar month, "
             "excluding refunded payments, event gratuity, and "
             "unclaimed tips (from non-tip-eligible departments). "
             "Not stored — recomputed on read so it stays fresh.",
    )
    x_clover_auto_created = fields.Boolean(
        string="Auto-Created from Clover Sync",
        default=False,
        readonly=True,
        help="TRUE when this hr.employee was created by the Clover "
             "sync because a Clover cashier had no matching Odoo "
             "employee. Review these records to add department, "
             "manager, work phone, etc.",
    )

    @api.depends("x_clover_employee_ids")
    def _compute_x_clover_tips_this_period(self):
        Tip = self.env["clover.tip.entry"].sudo()
        period_start = fields.Datetime.now().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0)
        for emp in self:
            tips = Tip.search([
                ("employee_id", "=", emp.id),
                ("date", ">=", period_start),
                ("is_refunded", "=", False),
                ("is_event_gratuity", "=", False),
                ("is_unclaimed", "=", False),
            ])
            emp.x_clover_tips_this_period = sum(tips.mapped("amount"))
