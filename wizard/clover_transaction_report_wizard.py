# -*- coding: utf-8 -*-
"""Wizard to generate Clover Transaction Log reports.

Supports three output modes:
* **View** — opens a filtered list view in the browser
* **PDF** — prints a QWeb report with totals
* **CSV** — downloads a spreadsheet-ready file
"""
import base64
import csv
import io
import logging
from datetime import date, datetime, time

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CloverTransactionReportWizard(models.TransientModel):
    _name = 'clover.transaction.report.wizard'
    _description = 'Clover Transaction Log Report Wizard'

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------
    date_from = fields.Date(
        'From',
        required=True,
        default=lambda self: date.today().replace(day=1),
    )
    date_to = fields.Date(
        'To',
        required=True,
        default=fields.Date.context_today,
    )
    state_filter = fields.Selection([
        ('all', 'All States'),
        ('done', 'Confirmed / Done'),
        ('authorized', 'Authorized'),
        ('pending', 'Pending'),
        ('cancel', 'Canceled'),
        ('error', 'Error'),
    ], string='Transaction State', default='all', required=True)

    output_type = fields.Selection([
        ('view', 'Open in Browser'),
        ('pdf', 'Download PDF'),
        ('csv', 'Download CSV'),
    ], string='Output', default='view', required=True)

    # CSV download fields (populated after generate)
    csv_file = fields.Binary('CSV File', readonly=True)
    csv_filename = fields.Char('Filename', readonly=True)

    # ------------------------------------------------------------------
    # Domain builder
    # ------------------------------------------------------------------
    def _build_domain(self):
        """Return a search domain for payment.transaction."""
        self.ensure_one()
        # Bracket the user-supplied Date range with full-day datetimes so
        # transactions created at any point during ``date_to`` are included.
        start_dt = datetime.combine(self.date_from, time.min)
        end_dt = datetime.combine(self.date_to, time.max)
        domain = [
            ('provider_code', '=', 'clover'),
            ('create_date', '>=', start_dt),
            ('create_date', '<=', end_dt),
        ]
        if self.state_filter and self.state_filter != 'all':
            domain.append(('state', '=', self.state_filter))
        return domain

    def _get_transactions(self):
        """Fetch payment.transaction records matching the wizard filters."""
        return self.env['payment.transaction'].search(
            self._build_domain(),
            order='create_date desc',
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_generate(self):
        """Dispatch to the selected output type."""
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("'From' date must be before 'To' date."))

        if self.output_type == 'view':
            return self._action_open_list()
        elif self.output_type == 'pdf':
            return self._action_print_pdf()
        elif self.output_type == 'csv':
            return self._action_download_csv()

    def _action_open_list(self):
        """Open a filtered list view of Clover transactions."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Clover Transaction Log'),
            'res_model': 'payment.transaction',
            'view_mode': 'list,form',
            'domain': self._build_domain(),
            'context': {'search_default_group_state': 1},
            'target': 'current',
        }

    def _action_print_pdf(self):
        """Generate the QWeb PDF report."""
        transactions = self._get_transactions()
        if not transactions:
            raise UserError(_("No Clover transactions found for the selected period."))

        data = {
            'wizard_id': self.id,
            'date_from': str(self.date_from),
            'date_to': str(self.date_to),
            'state_filter': self.state_filter,
            'transaction_ids': transactions.ids,
        }
        return self.env.ref(
            'payment_clover.action_report_clover_transaction_log'
        ).report_action(self, data=data)

    def _action_download_csv(self):
        """Build a CSV file and return a download action."""
        transactions = self._get_transactions()
        if not transactions:
            raise UserError(_("No Clover transactions found for the selected period."))

        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow([
            'Date',
            'Reference',
            'Clover Charge ID',
            'Customer',
            'Operation',
            'State',
            'Amount',
            'Currency',
            'Provider Reference',
            'State Message',
        ])

        total_amount = 0.0
        for tx in transactions:
            writer.writerow([
                tx.create_date.strftime('%Y-%m-%d %H:%M:%S') if tx.create_date else '',
                tx.reference or '',
                tx.clover_charge_id or '',
                tx.partner_name or '',
                dict(tx._fields['operation'].selection).get(tx.operation, tx.operation or ''),
                dict(tx._fields['state'].selection).get(tx.state, tx.state or ''),
                f'{tx.amount:.2f}',
                tx.currency_id.name if tx.currency_id else 'USD',
                tx.provider_reference or '',
                (tx.state_message or '')[:200],
            ])
            total_amount += tx.amount

        # Summary row
        writer.writerow([])
        writer.writerow([
            f'Period: {self.date_from} to {self.date_to}',
            f'Total Transactions: {len(transactions)}',
            '',
            '',
            '',
            '',
            f'{total_amount:.2f}',
            'USD',
            '',
            '',
        ])

        csv_data = output.getvalue()
        output.close()

        filename = f'clover_transactions_{self.date_from}_{self.date_to}.csv'
        self.write({
            'csv_file': base64.b64encode(csv_data.encode('utf-8')),
            'csv_filename': filename,
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content?model=clover.transaction.report.wizard'
                   f'&id={self.id}&field=csv_file'
                   f'&filename_field=csv_filename&download=true',
            'target': 'new',
        }
