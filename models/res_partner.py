# -*- coding: utf-8 -*-
"""Res partner extensions for Clover customer dedup.

The Clover customer_id we stamp here is the strongest identity
signal available: if a partner has one, subsequent syncs match on
it directly and never fall back to fuzzy phone/email matching. This
prevents the classic dedup failure where the same person shows up
twice — once with only a phone, once with only an email — and the
sync creates two res.partner rows.
"""
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_clover_customer_id = fields.Char(
        string="Clover Customer ID",
        index=True,
        copy=False,
        help="Stamped by the Clover sales sync when this contact is "
             "matched or auto-created for a Clover POS customer. "
             "Used as the primary dedup key on subsequent syncs so "
             "the same Clover customer never spawns duplicate "
             "contacts. Leave blank to opt this contact out of "
             "Clover auto-matching.",
    )
