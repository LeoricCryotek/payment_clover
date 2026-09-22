# -*- coding: utf-8 -*-
"""Defensive patch for Odoo 19 board.board drag-and-drop crash.

Odoo 19's `board.board` client-side JS calls
`ir.ui.view.edit_custom()` whenever a user drags a dashboard widget
to rearrange the layout. In this Odoo build the JS side passes ONLY
the new arch string and omits the `custom_id` argument the server
signature requires, which produces:

    TypeError: View.edit_custom() missing 1 required positional
    argument: 'custom_id'

…and 500s the entire dashboard on every drag attempt. Rather than
let the crash surface to end users, we override the method to make
both arguments optional. When either is missing (the buggy path)
we silently no-op: the dashboard stays usable, though the widget
layout won't persist across sessions until the upstream JS bug is
fixed. When both are passed correctly, we defer to core.

Scope is intentionally narrow — this only affects the one method
that is broken; every other ir.ui.view method routes through core
unchanged.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class IrUiView(models.Model):
    _inherit = "ir.ui.view"

    @api.model
    def edit_custom(self, custom_id=None, arch=None):
        if custom_id is None or arch is None:
            _logger.info(
                "payment_clover: swallowed board.board edit_custom "
                "call with custom_id=%r arch_present=%s "
                "(Odoo 19 dashboard drag-and-drop bug).",
                custom_id, arch is not None,
            )
            return True
        return super().edit_custom(custom_id, arch)
