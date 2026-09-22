# -*- coding: utf-8 -*-
"""Tip-report wizard.

Aggregates rows from ``clover.tip.entry`` for a chosen date range,
optionally scoped to one or more hr.departments and/or a specific
employee subset, and delivers the result as either an on-screen /
downloadable PDF or an emailed attachment.

Report structure:

    Department A
        Employee X — total, personal, unclaimed
            (optional per-day breakdown)
        Employee Y — ...
    Department B
        ...
    Unassigned                   (tips whose employee_id is empty)
    Grand Total

Filter semantics:

    * ``date_from`` inclusive; ``date_to`` inclusive to end-of-day
      (the wizard adds the 23:59:59 tail automatically).
    * ``department_ids`` empty  → every department in the DB.
    * ``employee_ids`` empty    → every employee that has tips in
                                   the selected departments.
    * ``include_gratuity``      → include pooled event-gratuity
                                   entries. Default False (they
                                   are pool money, not individual
                                   tips).
    * ``include_unclaimed``     → include entries flagged
                                   is_unclaimed (dept isn't
                                   tip-eligible). Default True
                                   because managers usually want
                                   to see who's collecting
                                   unclaimed tips to reallocate.
    * ``include_refunded``      → include refunded entries. Default
                                   False.

Emails are sent via ``mail.mail`` (not ``mail.template``) so the
recipient can be any comma-separated address string — no
res.partner records required. The PDF is attached in-line.
"""
import base64
import logging
from datetime import datetime, time

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CloverTipReportWizard(models.TransientModel):
    _name = "clover.tip.report.wizard"
    _description = "Clover Tip Report Wizard"

    date_from = fields.Date(
        required=True,
        default=lambda self: fields.Date.today().replace(day=1),
    )
    date_to = fields.Date(
        required=True,
        default=fields.Date.today,
    )
    department_ids = fields.Many2many(
        "hr.department",
        string="Departments",
        help="Leave empty to include every department. Only "
             "employees belonging to the selected departments are "
             "counted.",
    )
    employee_ids = fields.Many2many(
        "hr.employee",
        string="Employees",
        help="Leave empty to include every employee in the "
             "selected departments.",
    )
    group_by_day = fields.Boolean(
        string="Per-Day Breakdown",
        default=True,
        help="When on, each employee's tips are broken out day by "
             "day inside the report. When off, only the summary "
             "totals per employee are shown.",
    )
    include_gratuity = fields.Boolean(
        string="Include Pooled Event Gratuity",
        default=False,
        help="Include entries flagged as event gratuity — pooled "
             "money, not individual tips.",
    )
    include_unclaimed = fields.Boolean(
        string="Include Unclaimed Tips",
        default=True,
        help="Include tips collected by employees in "
             "non-tip-eligible departments (e.g. Volunteers). "
             "Shown so managers can reallocate.",
    )
    include_refunded = fields.Boolean(
        string="Include Refunded Tips",
        default=False,
    )
    email_to = fields.Char(
        string="Email Recipients",
        help="Comma-separated email addresses. When set, clicking "
             "\"Email Report\" will send the PDF to these "
             "recipients.",
    )
    email_subject = fields.Char(
        string="Email Subject",
        default=lambda self: _("Clover Tip Report"),
    )
    email_body = fields.Html(
        string="Email Message",
        default=lambda self: (
            "<p>Attached is the Clover tip report for the period "
            "selected. Numbers come from Clover-side payment tips "
            "and do not include cash tips left off-ticket.</p>"
        ),
    )

    # ---------------------------------------------------------------
    # Aggregation
    # ---------------------------------------------------------------
    def _get_domain(self):
        self.ensure_one()
        dt_from = datetime.combine(self.date_from, time.min)
        dt_to = datetime.combine(self.date_to, time.max)
        domain = [("date", ">=", dt_from), ("date", "<=", dt_to)]
        if not self.include_gratuity:
            domain.append(("is_event_gratuity", "=", False))
        if not self.include_unclaimed:
            domain.append(("is_unclaimed", "=", False))
        if not self.include_refunded:
            domain.append(("is_refunded", "=", False))
        if self.employee_ids:
            domain.append(("employee_id", "in", self.employee_ids.ids))
        elif self.department_ids:
            # No explicit employees — pull every employee in the
            # selected departments.
            emps = self.env["hr.employee"].sudo().search([
                ("department_id", "in", self.department_ids.ids),
            ])
            # Include unassigned tips too so the report shows them
            # under "Unassigned" — filtering only on employees
            # here would hide them.
            if emps:
                domain.append((
                    "|",
                    ("employee_id", "in", emps.ids),
                    ("employee_id", "=", False),
                ))
            else:
                domain.append(("employee_id", "=", False))
        return domain

    def _compute_report_data(self):
        """Build the nested dict fed to the QWeb template."""
        self.ensure_one()
        Tip = self.env["clover.tip.entry"].sudo()
        domain = self._get_domain()
        entries = Tip.search(domain, order="date")

        # Bucket by department → employee → day
        by_dept = {}
        grand_total = 0.0
        grand_personal = 0.0
        grand_unclaimed = 0.0
        grand_gratuity = 0.0

        for entry in entries:
            emp = entry.employee_id
            dept = emp.department_id if emp else False
            dept_key = dept.id if dept else 0
            dept_name = (dept.name if dept
                         else (_("Unassigned") if not emp
                               else _("(No Department)")))
            emp_key = emp.id if emp else 0
            emp_name = emp.name if emp else _("Unassigned")

            day = fields.Date.to_string(entry.date.date())

            dept_bucket = by_dept.setdefault(dept_key, {
                "name": dept_name,
                "employees": {},
                "total": 0.0,
                "personal": 0.0,
                "unclaimed": 0.0,
                "gratuity": 0.0,
            })
            emp_bucket = dept_bucket["employees"].setdefault(emp_key, {
                "name": emp_name,
                "days": {},
                "total": 0.0,
                "personal": 0.0,
                "unclaimed": 0.0,
                "gratuity": 0.0,
            })
            day_bucket = emp_bucket["days"].setdefault(day, {
                "total": 0.0,
                "personal": 0.0,
                "unclaimed": 0.0,
                "gratuity": 0.0,
                "count": 0,
            })

            amt = entry.amount or 0.0
            day_bucket["total"] += amt
            day_bucket["count"] += 1
            emp_bucket["total"] += amt
            dept_bucket["total"] += amt
            grand_total += amt

            if entry.is_event_gratuity:
                day_bucket["gratuity"] += amt
                emp_bucket["gratuity"] += amt
                dept_bucket["gratuity"] += amt
                grand_gratuity += amt
            elif entry.is_unclaimed:
                day_bucket["unclaimed"] += amt
                emp_bucket["unclaimed"] += amt
                dept_bucket["unclaimed"] += amt
                grand_unclaimed += amt
            else:
                day_bucket["personal"] += amt
                emp_bucket["personal"] += amt
                dept_bucket["personal"] += amt
                grand_personal += amt

        # Sort departments alphabetically (with "Unassigned" last),
        # employees by descending total within each department.
        def _dept_sort_key(item):
            dept_id, data = item
            if dept_id == 0:
                return (1, "")  # push Unassigned to the end
            return (0, data["name"].lower())

        depts_sorted = []
        for dept_id, dept in sorted(by_dept.items(),
                                     key=_dept_sort_key):
            emps_sorted = sorted(
                dept["employees"].values(),
                key=lambda e: -e["total"])
            for emp in emps_sorted:
                emp["days_sorted"] = [
                    {"day": d, **v}
                    for d, v in sorted(emp["days"].items())
                ]
            dept["employees_sorted"] = emps_sorted
            depts_sorted.append(dept)

        return {
            "wizard": self,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "departments": depts_sorted,
            "grand_total": grand_total,
            "grand_personal": grand_personal,
            "grand_unclaimed": grand_unclaimed,
            "grand_gratuity": grand_gratuity,
            "generated_at": fields.Datetime.now(),
        }

    # ---------------------------------------------------------------
    # Report actions
    # ---------------------------------------------------------------
    def action_generate_pdf(self):
        """Render and return the PDF report as a download."""
        self.ensure_one()
        report_ref = "payment_clover.action_report_clover_tips"
        return self.env.ref(report_ref).report_action(self)

    def action_send_email(self):
        """Render the PDF and email it to `email_to` recipients."""
        self.ensure_one()
        if not self.email_to:
            raise UserError(_(
                "Enter one or more recipient email addresses "
                "before clicking Email Report."))

        report = self.env.ref(
            "payment_clover.action_report_clover_tips")
        pdf_content, _pdf_type = report._render_qweb_pdf(
            report.report_name, res_ids=self.ids)

        filename = (
            f"Clover_Tips_"
            f"{self.date_from.strftime('%Y%m%d')}_"
            f"{self.date_to.strftime('%Y%m%d')}.pdf"
        )

        attachment = self.env["ir.attachment"].sudo().create({
            "name": filename,
            "type": "binary",
            "datas": base64.b64encode(pdf_content),
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": "application/pdf",
        })

        # Split recipients on commas / semicolons / whitespace,
        # keep anything that looks vaguely like an email.
        raw = self.email_to.replace(";", ",").replace("\n", ",")
        recipients = [
            addr.strip() for addr in raw.split(",")
            if "@" in addr.strip()
        ]
        if not recipients:
            raise UserError(_(
                "No valid email addresses found in \"%s\"."
            ) % self.email_to)

        mail = self.env["mail.mail"].sudo().create({
            "subject": self.email_subject or _("Clover Tip Report"),
            "body_html": self.email_body or "",
            "email_to": ", ".join(recipients),
            "email_from": (self.env.user.email
                           or self.env.company.email
                           or False),
            "attachment_ids": [(6, 0, [attachment.id])],
            "auto_delete": True,
        })
        mail.send()

        _logger.info(
            "payment_clover: emailed tip report %s to %s",
            filename, recipients,
        )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Tip Report Sent"),
                "message": _("PDF emailed to: %s") % ", ".join(
                    recipients),
                "type": "success",
                "sticky": False,
            },
        }
