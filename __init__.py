# -*- coding: utf-8 -*-
# Part of Elks Lodge Odoo Modules. See LICENSE file for full copyright and licensing details.

import logging

from . import controllers
from . import models
from . import wizard

from odoo.addons.payment import setup_provider, reset_payment_provider


_logger = logging.getLogger(__name__)


def _activate_and_link_card(env):
    """Ensure the 'card' payment.method is active AND linked to every
    Clover provider in this DB.

    Odoo 19's ``payment._setup_provider`` only handles multi-company
    copy; it does NOT activate or link the default payment methods
    declared via ``_get_default_payment_method_codes``. Without this
    step, a fresh install leaves Card both inactive and unlinked, so
    the terminal's ``payment_method_id`` lookup fails on the first
    charge and the constraint that requires Card.provider_ids to
    contain at least one non-disabled provider prevents activation
    from the UI either.

    Idempotent — safe to call on initial install (via post_init_hook)
    AND on upgrade (via migrations/<version>/post-migration.py).

    Order matters:
      1. Link the provider on the M2M (no constraint triggered on the
         M2M write itself).
      2. Activate Card — by now its ``provider_ids`` has the enabled
         Clover provider, so ``_check_provider_state`` passes.
    """
    providers = env['payment.provider'].search([('code', '=', 'clover')])
    if not providers:
        _logger.info(
            "payment_clover: no Clover providers in DB, "
            "skipping Card method linkage."
        )
        return

    card = env['payment.method'].with_context(active_test=False).search(
        [('code', '=', 'card')], limit=1,
    )
    if not card:
        _logger.warning(
            "payment_clover: no 'card' payment.method record exists; "
            "base `payment` module may need reinstalling. Skipping link."
        )
        return

    for provider in providers:
        if card not in provider.payment_method_ids:
            provider.sudo().write({
                'payment_method_ids': [(4, card.id)],
            })
            _logger.info(
                "payment_clover: linked 'card' to Clover provider "
                "id=%s (company=%s).",
                provider.id, provider.company_id.name or '?',
            )

    # Force the M2M to flush so the activate-time constraint sees the
    # new link.
    card.invalidate_recordset(['provider_ids'])

    if not card.active:
        card.sudo().write({'active': True})
        _logger.info(
            "payment_clover: activated payment.method 'card' (id=%s).",
            card.id,
        )


def post_init_hook(env):
    setup_provider(env, 'clover')
    _activate_and_link_card(env)


def uninstall_hook(env):
    reset_payment_provider(env, 'clover')
