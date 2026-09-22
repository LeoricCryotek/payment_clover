# -*- coding: utf-8 -*-
"""Defensive patch for Odoo 19 board.board drag-and-drop crash.

Odoo 19's ``board.board`` client-side JS calls
``View.edit_custom()`` — the HTTP controller in
``odoo/addons/web/controllers/view.py`` — whenever a user drags a
dashboard widget to rearrange the layout. In this Odoo build the
JS side passes ONLY the new ``arch`` string and omits the
``custom_id`` argument the server signature requires, which
produces:

    TypeError: View.edit_custom() missing 1 required positional
    argument: 'custom_id'

…and 500s the entire dashboard on every drag attempt. Rather than
let the crash surface to end users, we subclass the ``View``
controller and re-declare ``edit_custom`` with both arguments
optional. When either is missing (the buggy path) we silently
no-op — the widget layout won't persist across sessions until the
upstream JS bug is fixed, but the dashboard stays usable. When
both are passed correctly, we defer to core.

NOTE: The previous attempt to fix this via a ``models.ir_ui_view``
override was ineffective because ``edit_custom`` here is a
Controller method (routed through http.py's dispatcher), not a
Model method. Only the Controller subclass wins the JSON-RPC
route.
"""
import logging

from odoo.addons.web.controllers.view import View

_logger = logging.getLogger(__name__)


class CloverPatchedView(View):

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
