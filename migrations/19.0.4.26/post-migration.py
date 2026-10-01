# -*- coding: utf-8 -*-
"""One-shot repair for orphan tips.

Before 19.0.4.26, a Clover tip row's employee attribution came
exclusively from the ``payment.employee.id`` field in Clover's
Ecommerce API. For card payments on shared terminals, that field
is often blank even though the parent order DID identify a cashier
— so the tip was synced with ``clover_employee_id = False`` and
``employee_id = False`` even when the parent clover.sale was
correctly attributed.

This migration walks every orphan tip (``clover_employee_id =
False`` AND parent sale has a non-false ``clover_employee_id``)
and copies the sale's attribution onto the tip. Also recomputes
``is_unclaimed`` against the newly-assigned employee's department
tip-eligibility.

Safe to re-run — only touches rows that need it.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info(
        "payment_clover 19.0.4.26: backfilling orphan tips from "
        "their parent sale's cashier attribution.",
    )

    # Fast path: do the clover_employee_id + employee_id copy via
    # a single UPDATE. Then let a Python pass recompute
    # is_unclaimed, since that depends on department.
    cr.execute("""
        UPDATE clover_tip_entry t
           SET clover_employee_id = s.clover_employee_id,
               employee_id        = s.employee_id
          FROM clover_sale s
         WHERE t.clover_sale_id = s.id
           AND t.clover_employee_id IS NULL
           AND s.clover_employee_id IS NOT NULL
    """)
    _logger.info(
        "payment_clover 19.0.4.26: SQL-backfilled "
        "clover_employee_id + employee_id on %s orphan tips.",
        cr.rowcount,
    )

    # Second pass — recompute is_unclaimed against the new
    # employee's department. Done in Python so we don't have to
    # replicate the department join in SQL.
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    Tip = env["clover.tip.entry"].sudo()

    # Only touch tips that just got an employee + whose
    # is_unclaimed state might now be wrong.
    tips = Tip.search([
        ("employee_id", "!=", False),
    ])
    changed = 0
    for t in tips:
        dept = t.employee_id.department_id
        should = bool(dept and not dept.x_clover_tips_eligible)
        if t.is_unclaimed != should:
            t.write({"is_unclaimed": should})
            changed += 1
    _logger.info(
        "payment_clover 19.0.4.26: recomputed is_unclaimed on "
        "%s tips.", changed,
    )
