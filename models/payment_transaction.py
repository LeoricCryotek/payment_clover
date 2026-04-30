# -*- coding: utf-8 -*-
# Part of Elks Lodge Odoo Modules. See LICENSE file for full copyright and licensing details.

import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment_clover import const
from odoo.addons.payment_clover.controllers.main import CloverController


_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    """Extend payment.transaction with Clover charge processing.

    Handles tokenized card charges via Clover's v1/charges API,
    stores the Clover charge ID for refund/void operations, and
    manages transaction state transitions (pending → done/error).
    """
    _inherit = "payment.transaction"

    # Store the Clover charge ID for refunds / capture
    clover_charge_id = fields.Char(
        "Clover Charge ID", readonly=True, copy=False,
    )

    # ------------------------------------------------------------------
    # Processing values (passed to inline form JS)
    # ------------------------------------------------------------------

    def _get_specific_processing_values(self, processing_values):
        """Override of `payment` to return Clover-specific processing values.

        These values are consumed by the inline form JavaScript to set up
        the Clover iframe tokenizer.
        """
        if self.provider_code != "clover":
            return super()._get_specific_processing_values(processing_values)

        inline_values = self.provider_id._clover_get_inline_form_values()
        base_url = self.provider_id.get_base_url()
        return {
            "clover_pakms_key": inline_values["pakms_key"],
            "clover_merchant_id": inline_values.get("merchant_id", ""),
            "clover_sdk_url": inline_values["sdk_url"],
            "return_url": f"{base_url}{CloverController._return_url}",
        }

    # ------------------------------------------------------------------
    # Payment request (charge creation)
    # ------------------------------------------------------------------

    def _clover_build_description(self):
        """Build a human-readable description for the Clover charge.

        Priority:
        1. Terminal — description passed via context (item name / staff note)
        2. Invoice — "INV/2026/00015: Dues, Event Ticket" (line names)
        3. Sale order — "S00042: Dues, Event Ticket" (line names)
        4. Fallback — the Odoo transaction reference
        """
        # 1) Terminal description (staff entered or auto-filled from item)
        ctx_desc = self.env.context.get("clover_charge_description", "")
        if ctx_desc:
            return f"{self.reference} — {ctx_desc}"

        # 2) Invoice lines (account_payment adds invoice_ids)
        if hasattr(self, "invoice_ids") and self.invoice_ids:
            inv = self.invoice_ids[0]
            lines = inv.invoice_line_ids.filtered(
                lambda l: l.display_type == "product"
            )
            if lines:
                items = ", ".join(lines.mapped("name")[:10])
                return f"{inv.name}: {items}"
            return f"{inv.name}: {self.reference}"

        # 3) Sale order lines (sale module adds sale_order_ids)
        if hasattr(self, "sale_order_ids") and self.sale_order_ids:
            so = self.sale_order_ids[0]
            lines = so.order_line.filtered(
                lambda l: not l.display_type
            )
            if lines:
                items = ", ".join(lines.mapped("name")[:10])
                return f"{so.name}: {items}"
            return f"{so.name}: {self.reference}"

        # 4) Fallback
        return self.reference

    def _send_payment_request(self):
        """Override of `payment` to create a Clover charge."""
        if self.provider_code != "clover":
            return super()._send_payment_request()

        # The source token was passed via the controller
        source_token = self.env.context.get("clover_source_token")
        if not source_token:
            self._set_error(_("No card token received from Clover."))
            return

        amount_minor = payment_utils.to_minor_currency_units(
            self.amount, self.currency_id
        )
        payload = {
            "amount": amount_minor,
            "currency": self.currency_id.name.lower(),
            "source": source_token,
            "description": self._clover_build_description(),
            "external_reference_id": self.reference,
            "capture": not self.provider_id.capture_manually,
        }

        if self.partner_email:
            payload["receipt_email"] = self.partner_email

        try:
            response = self.provider_id._clover_make_request(
                "POST", "v1/charges", payload=payload,
                idempotency_key=payment_utils.generate_idempotency_key(
                    self, scope="charges"
                ),
            )
        except ValidationError as e:
            self._set_error(str(e))
            return

        # Build payment_data and process
        payment_data = {
            "reference": self.reference,
            "charge": response,
        }
        self._process("clover", payment_data)

    # ------------------------------------------------------------------
    # Capture (for manual capture mode)
    # ------------------------------------------------------------------

    def _send_capture_request(self):
        """Override of `payment` to capture a Clover authorization."""
        if self.provider_code != "clover":
            return super()._send_capture_request()

        charge_id = self.source_transaction_id.clover_charge_id
        if not charge_id:
            self._set_error(_("No Clover charge ID found for capture."))
            return

        amount_minor = payment_utils.to_minor_currency_units(
            self.amount, self.currency_id
        )
        try:
            response = self.provider_id._clover_make_request(
                "POST",
                f"v1/charges/{charge_id}/capture",
                payload={"amount": amount_minor},
            )
        except ValidationError as e:
            self._set_error(str(e))
            return

        payment_data = {
            "reference": self.reference,
            "charge": response,
        }
        self._process("clover", payment_data)

    # ------------------------------------------------------------------
    # Void
    # ------------------------------------------------------------------

    def _send_void_request(self):
        """Override of `payment` to void a Clover authorization."""
        if self.provider_code != "clover":
            return super()._send_void_request()

        charge_id = self.source_transaction_id.clover_charge_id
        if not charge_id:
            self._set_error(_("No Clover charge ID found for void."))
            return

        # Clover doesn't have a dedicated void; refund the auth amount
        try:
            response = self.provider_id._clover_make_request(
                "POST",
                f"v1/charges/{charge_id}/refunds",
            )
        except ValidationError as e:
            self._set_error(str(e))
            return

        payment_data = {
            "reference": self.reference,
            "refund": response,
            "is_void": True,
        }
        self._process("clover", payment_data)

    # ------------------------------------------------------------------
    # Refunds
    # ------------------------------------------------------------------

    def _send_refund_request(self):
        """Override of `payment` to send a refund request to Clover."""
        if self.provider_code != "clover":
            return super()._send_refund_request()

        charge_id = self.source_transaction_id.clover_charge_id
        if not charge_id:
            self._set_error(_("No Clover charge ID found for refund."))
            return

        amount_minor = payment_utils.to_minor_currency_units(
            -self.amount,  # Refund txs have negative amount
            self.currency_id,
        )
        payload = {"amount": amount_minor} if amount_minor else {}

        try:
            response = self.provider_id._clover_make_request(
                "POST",
                f"v1/charges/{charge_id}/refunds",
                payload=payload,
            )
        except ValidationError as e:
            self._set_error(str(e))
            return

        payment_data = {
            "reference": self.reference,
            "refund": response,
        }
        self._process("clover", payment_data)

    # ------------------------------------------------------------------
    # Transaction search / matching
    # ------------------------------------------------------------------

    @api.model
    def _search_by_reference(self, provider_code, payment_data):
        """Override of `payment` to find the transaction by Clover data."""
        if provider_code != "clover":
            return super()._search_by_reference(provider_code, payment_data)

        reference = payment_data.get("reference")
        if reference:
            tx = self.search([
                ("reference", "=", reference),
                ("provider_code", "=", "clover"),
            ])
        else:
            _logger.warning("Received Clover data with missing reference")
            tx = self
        if not tx:
            _logger.warning(
                "No Clover transaction found for reference %s", reference
            )
        return tx

    # ------------------------------------------------------------------
    # Amount extraction (for validation)
    # ------------------------------------------------------------------

    def _extract_amount_data(self, payment_data):
        """Override of `payment` to extract amount from Clover data."""
        if self.provider_code != "clover":
            return super()._extract_amount_data(payment_data)

        charge = payment_data.get("charge") or payment_data.get("refund", {})
        amount_minor = charge.get("amount", 0)
        currency_code = charge.get("currency", "").upper()
        amount = payment_utils.to_major_currency_units(
            amount_minor, self.currency_id
        )
        return {
            "amount": amount,
            "currency_code": currency_code,
        }

    # ------------------------------------------------------------------
    # State update (the core processing method)
    # ------------------------------------------------------------------

    def _apply_updates(self, payment_data):
        """Override of `payment` to update the transaction from Clover data.

        This is called by ``_process()`` after reference matching and
        amount validation.  It reads the Clover charge/refund status and
        transitions the Odoo transaction to the corresponding state.
        """
        if self.provider_code != "clover":
            return super()._apply_updates(payment_data)

        is_refund = "refund" in payment_data and "charge" not in payment_data
        is_void = payment_data.get("is_void", False)

        if is_refund or is_void:
            refund_data = payment_data.get("refund", {})
            self.provider_reference = refund_data.get("id", "")
            status = refund_data.get("status", "")
        else:
            charge = payment_data.get("charge", {})
            self.provider_reference = charge.get("id", "")
            self.clover_charge_id = charge.get("id", "")
            status = charge.get("status", "")
            # Check if auth-only (captured=false)
            captured = charge.get("captured", True)

        if not status:
            self._set_error(_(
                "Received Clover data with missing payment status."
            ))
            return

        if status in const.STATUS_MAPPING["done"]:
            if not is_refund and not is_void:
                # Check for auth-only vs captured
                if not payment_data.get("charge", {}).get("captured", True):
                    self._set_authorized()
                else:
                    self._set_done()
            else:
                self._set_done()
                if is_refund:
                    self.env.ref(
                        "payment.cron_post_process_payment_tx"
                    )._trigger()
        elif status in const.STATUS_MAPPING["pending"]:
            self._set_pending()
        elif status in const.STATUS_MAPPING["cancel"]:
            self._set_canceled()
        elif status in const.STATUS_MAPPING["error"]:
            error_msg = (
                payment_data.get("charge", {})
                .get("failure_message", "")
            ) or _("The payment was declined by Clover.")
            self._set_error(error_msg)
        else:
            _logger.warning(
                "Unknown Clover status '%s' for transaction %s",
                status, self.reference,
            )
            self._set_error(_(
                "Received unknown payment status from Clover: %s", status
            ))
