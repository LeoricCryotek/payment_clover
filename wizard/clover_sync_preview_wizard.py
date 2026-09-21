# -*- coding: utf-8 -*-
"""Dry-run preview of a Clover sync.

Fetches the last N Clover orders + all Clover employees, runs the
same matching / dedup / auto-create decision logic as the real sync
IN MEMORY ONLY, and shows the admin what would happen. Commits
nothing. If the admin is happy, they hit "Commit These Changes" and
the real sync runs over the SAME window.

Purpose: catch mis-linked employees, wrong-partner dedup, sparse
auto-creates, and misconfigured Float / department flags BEFORE
they land in the database.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class CloverSyncPreviewWizard(models.TransientModel):
    _name = "clover.sync.preview.wizard"
    _description = "Clover Sync — Preview / Dry Run"

    provider_id = fields.Many2one(
        "payment.provider",
        string="Clover Provider",
        required=True,
        domain=[("code", "=", "clover")],
    )
    sample_size = fields.Integer(
        string="Sample Size (recent orders)",
        default=25,
        help="How many recent Clover orders to include in the "
             "preview. Employees always sync fully (there aren't "
             "that many). Bigger sample = slower.",
    )
    fetched_at = fields.Datetime(
        string="Preview Fetched At",
        readonly=True,
    )

    employee_line_ids = fields.One2many(
        "clover.sync.preview.employee",
        "wizard_id",
        readonly=True,
    )
    sale_line_ids = fields.One2many(
        "clover.sync.preview.sale",
        "wizard_id",
        readonly=True,
    )

    # ---- Summary counters (all computed from the line records) ----
    count_emp_link_existing = fields.Integer(
        string="Employees: link to existing",
        compute="_compute_summary")
    count_emp_auto_create = fields.Integer(
        string="Employees: auto-create hr.employee",
        compute="_compute_summary")
    count_emp_already_linked = fields.Integer(
        string="Employees: already linked (no change)",
        compute="_compute_summary")
    count_emp_float = fields.Integer(
        string="Employees: Float / excluded",
        compute="_compute_summary")

    count_sale_create = fields.Integer(
        string="Sales: create new mirror",
        compute="_compute_summary")
    count_sale_update = fields.Integer(
        string="Sales: update existing mirror",
        compute="_compute_summary")
    count_partner_match = fields.Integer(
        string="Sales: match existing contact",
        compute="_compute_summary")
    count_partner_create = fields.Integer(
        string="Sales: auto-create contact",
        compute="_compute_summary")
    count_partner_skip = fields.Integer(
        string="Sales: skipped (no contact info)",
        compute="_compute_summary")

    @api.depends("employee_line_ids", "sale_line_ids")
    def _compute_summary(self):
        for wiz in self:
            emp = wiz.employee_line_ids
            sale = wiz.sale_line_ids
            wiz.count_emp_link_existing = len(
                emp.filtered(lambda l: l.action == "link"))
            wiz.count_emp_auto_create = len(
                emp.filtered(lambda l: l.action == "create"))
            wiz.count_emp_already_linked = len(
                emp.filtered(lambda l: l.action == "unchanged"))
            wiz.count_emp_float = len(
                emp.filtered(lambda l: l.action == "float"))
            wiz.count_sale_create = len(
                sale.filtered(lambda l: l.sale_action == "create"))
            wiz.count_sale_update = len(
                sale.filtered(lambda l: l.sale_action == "update"))
            wiz.count_partner_match = len(
                sale.filtered(lambda l: l.partner_action == "match"))
            wiz.count_partner_create = len(
                sale.filtered(lambda l: l.partner_action == "create"))
            wiz.count_partner_skip = len(
                sale.filtered(lambda l: l.partner_action == "skip"))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_refresh_preview(self):
        """Re-fetch the preview (re-runs the read-only decision logic)."""
        self.ensure_one()
        if not self.provider_id:
            return False
        # Clear existing lines.
        self.employee_line_ids.unlink()
        self.sale_line_ids.unlink()
        # Pull decisions from the provider.
        emp_decisions, sale_decisions = self.provider_id._clover_preview_sync(
            sample_size=self.sample_size or 25,
        )
        self.env["clover.sync.preview.employee"].create([
            dict(d, wizard_id=self.id) for d in emp_decisions
        ])
        self.env["clover.sync.preview.sale"].create([
            dict(d, wizard_id=self.id) for d in sale_decisions
        ])
        self.fetched_at = fields.Datetime.now()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_commit_preview(self):
        """Run the REAL sync now.

        Uses the same pipeline the nightly cron uses. Not bounded to
        the preview sample — pulls everything since the last sync
        (or all, on first run). We warn about this on the button.
        """
        self.ensure_one()
        if not self.provider_id:
            return False
        stats = self.provider_id._clover_run_full_sync()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Clover Sync Complete"),
                "message": _(
                    "Employees: %(el)s linked, %(en)s new mirror rows, "
                    "%(hr)s hr.employees created. "
                    "Sales: %(s)s orders, %(t)s tip entries.",
                    el=stats.get("employees_linked", 0),
                    en=stats.get("employees_new", 0),
                    hr=stats.get("hr_created", 0),
                    s=stats.get("sales", 0),
                    t=stats.get("tips", 0),
                ),
                "type": "success",
                "sticky": True,
            },
        }


class CloverSyncPreviewEmployee(models.TransientModel):
    _name = "clover.sync.preview.employee"
    _description = "Clover Sync Preview — Employee Decision"
    _order = "action, name"

    wizard_id = fields.Many2one(
        "clover.sync.preview.wizard",
        required=True,
        ondelete="cascade",
    )
    clover_employee_id = fields.Char(string="Clover ID", readonly=True)
    name = fields.Char(readonly=True)
    nickname = fields.Char(readonly=True)
    email = fields.Char(readonly=True)
    role = fields.Char(readonly=True)
    action = fields.Selection(
        [
            ("link", "Link to existing hr.employee"),
            ("create", "Auto-create hr.employee"),
            ("unchanged", "Already linked — no change"),
            ("float", "Float / excluded"),
        ],
        string="Preview Action",
        readonly=True,
    )
    matched_employee_id = fields.Many2one(
        "hr.employee",
        string="Matched Employee",
        readonly=True,
    )
    match_reason = fields.Char(
        string="Match Reason",
        readonly=True,
        help="How the sync would decide the match "
             "(work_email, name, or blank for auto-create).",
    )


class CloverSyncPreviewSale(models.TransientModel):
    _name = "clover.sync.preview.sale"
    _description = "Clover Sync Preview — Sale Decision"
    _order = "date desc"

    wizard_id = fields.Many2one(
        "clover.sync.preview.wizard",
        required=True,
        ondelete="cascade",
    )
    clover_order_id = fields.Char(readonly=True)
    date = fields.Datetime(readonly=True)
    cashier_name = fields.Char(string="Clover Cashier", readonly=True)
    customer_name = fields.Char(readonly=True)
    customer_phone = fields.Char(readonly=True)
    customer_email = fields.Char(readonly=True)
    total_amount = fields.Float(digits=(12, 2), readonly=True)
    tip_amount = fields.Float(digits=(12, 2), readonly=True)
    line_count = fields.Integer(readonly=True)
    sale_action = fields.Selection(
        [
            ("create", "Create new mirror row"),
            ("update", "Update existing mirror row"),
        ],
        readonly=True,
    )
    partner_action = fields.Selection(
        [
            ("match", "Match existing contact"),
            ("create", "Auto-create contact"),
            ("skip", "Skip (no contact info or auto-create off)"),
        ],
        readonly=True,
    )
    matched_partner_id = fields.Many2one(
        "res.partner",
        string="Matched Contact",
        readonly=True,
    )
    partner_reason = fields.Char(
        string="Match Reason",
        readonly=True,
        help="clover_customer_id / phone / email / auto-create / "
             "skipped-sparse / skipped-toggle-off.",
    )
