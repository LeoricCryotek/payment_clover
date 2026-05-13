# -*- coding: utf-8 -*-
"""Self-heal: ensure the 'card' payment.method is active and linked to
every Clover provider in this database.

Background
----------
Odoo 19's ``payment.payment_provider._setup_provider`` (invoked by our
``post_init_hook → setup_provider(env, 'clover')``) only handles the
multi-company copy step. It does NOT activate or link the methods
declared via ``_get_default_payment_method_codes()``. As a result fresh
installs leave:

* ``Clover.payment_method_ids`` empty
* ``payment_method_card.active = False``
* ``payment_method_card.provider_ids`` without Clover

Three consequences:

1. ``payment.transaction.create()`` from our terminal controller fails
   the ``payment_method_id`` NOT NULL constraint.
2. The Configuration tab on the Clover provider shows the "→ Enable
   Payment Methods" link, but the relational list it opens is empty.
3. Manually flipping Card.active on the model form raises
   "Invalid Operation: This payment method needs a partner in crime;
   you should enable a payment provider supporting this method first."

This post-migration runs during ``button_immediate_upgrade`` (because
Odoo's migration machinery picks up ``migrations/<version>/*.py`` when
the manifest version bumps), closes all three gaps in one transaction,
and is idempotent — re-running it is a no-op once the link is in place.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Activate + link the Card method to every Clover provider.

    :param cr: database cursor passed by Odoo's migration runner
    :param version: previous installed version, or None on fresh install
    """
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})

    providers = env['payment.provider'].search([('code', '=', 'clover')])
    if not providers:
        _logger.info(
            "payment_clover migration 19.0.1.10: no Clover providers in "
            "this database, nothing to link."
        )
        return

    # `active_test=False` is required because payment_method_card ships
    # with active=False, and `search()` without it skips inactive rows.
    card = env['payment.method'].with_context(active_test=False).search(
        [('code', '=', 'card')], limit=1,
    )
    if not card:
        _logger.warning(
            "payment_clover migration 19.0.1.10: no 'card' payment.method "
            "record exists. Base `payment` module may need reinstalling. "
            "Skipping link step."
        )
        return

    # ORDER MATTERS: link the provider FIRST, activate Card SECOND.
    #
    # payment.method has a constraint (_check_provider_state) that
    # forbids active=True when no non-disabled provider supports the
    # method ("This payment method needs a partner in crime ..."). If
    # we flip active=True first while card.provider_ids is empty, the
    # constraint raises and the whole migration rolls back — exactly
    # the failure mode the first attempt at this script produced.
    #
    # Writing provider.payment_method_ids updates the M2M from both
    # ends, so after the loop card.provider_ids will include Clover
    # (state='enabled'), satisfying the constraint when we then flip
    # active to True.
    for provider in providers:
        if card not in provider.payment_method_ids:
            provider.sudo().write({
                'payment_method_ids': [(4, card.id)],
            })
            _logger.info(
                "payment_clover migration 19.0.1.10: linked 'card' to "
                "Clover provider id=%s (company=%s).",
                provider.id,
                provider.company_id.name or '?',
            )

    # Force the M2M write to flush so the constraint check on
    # `active = True` sees the up-to-date provider_ids.
    card.invalidate_recordset(['provider_ids'])

    if not card.active:
        card.sudo().write({'active': True})
        _logger.info(
            "payment_clover migration 19.0.1.10: activated "
            "payment.method 'card' (id=%s).",
            card.id,
        )
