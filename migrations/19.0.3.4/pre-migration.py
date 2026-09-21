# -*- coding: utf-8 -*-
"""Pre-migration for 19.0.3.4.

Changes ``payment_provider.clover_sync_hour`` from Integer to a
Selection whose keys are string versions of the same numbers
('0' .. '23'). PostgreSQL happily converts INTEGER → VARCHAR with a
USING clause, and Odoo's ORM will read the resulting string values
into the new Selection field without data loss.

If we don't do this pre-migration, Odoo drops the column when the
field type changes, and every provider's chosen sync hour resets
to the default (which was Integer 2 → Selection default '2', so
it looks the same but any admin who picked a different hour would
silently lose their setting).
"""


def migrate(cr, version):
    cr.execute("""
        SELECT data_type
        FROM information_schema.columns
        WHERE table_name = 'payment_provider'
          AND column_name = 'clover_sync_hour'
    """)
    row = cr.fetchone()
    if row and row[0] == "integer":
        cr.execute("""
            ALTER TABLE payment_provider
            ALTER COLUMN clover_sync_hour TYPE VARCHAR(4)
            USING clover_sync_hour::VARCHAR
        """)
