# -*- coding: utf-8 -*-
"""Self-heal: re-run the Card activation + linkage for installs that
landed at versions >= 19.0.1.10 and therefore never triggered the
original ``migrations/19.0.1.10/post-migration.py`` (because Odoo only
runs migrations whose target version is greater than the installed
version).

Symptom this addresses: a fresh local install at, say, 19.0.1.18 leaves
``payment_method_card.active = False`` and Clover not present in its
``provider_ids``, because Odoo 19's ``setup_provider`` no longer auto-
links default methods and the original linkage migration was skipped.

This script delegates to the same idempotent helper used by
``post_init_hook``, so the logic lives in one place.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    from odoo.addons.payment_clover import _activate_and_link_card

    env = api.Environment(cr, SUPERUSER_ID, {})
    _activate_and_link_card(env)
    _logger.info(
        "payment_clover migration 19.0.1.19: Card activation + linkage "
        "ensured (idempotent, no-op if already done)."
    )
