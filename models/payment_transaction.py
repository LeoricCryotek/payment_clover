# -*- coding: utf-8 -*-
# Part of Elks Lodge Odoo Modules. See LICENSE file for full copyright and licensing details.

import hashlib
import logging
import re

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
    # Clover's Ecommerce v1/charges API caps `external_reference_id`
    # at 12 characters (and validates a strict alphanumeric format —
    # hyphens are rejected). Odoo's default reference `tx-YYYYMMDDHHMMSS`
    # is 17 chars with a hyphen, so we generate a deterministic short
    # alias from the full reference, store it on the transaction, and
    # use that for both the outbound payload and for webhook lookups.
    clover_external_ref = fields.Char(
        "Clover External Ref",
        readonly=True,
        copy=False,
        help="Short alphanumeric reference (≤12 chars) sent to Clover as "
             "external_reference_id. Used to map Clover webhooks back "
             "to this transaction.",
    )
    clover_order_id = fields.Char(
        "Clover Order ID",
        readonly=True,
        copy=False,
        help="The Clover order created before the charge, so the Clover "
             "dashboard renders real line items instead of a generic "
             "'Item 1'. Linked to the charge via order_id in the charge "
             "payload.",
    )
    clover_cashier_id = fields.Many2one(
        "res.users",
        string="Cashier",
        readonly=True,
        copy=False,
        help="The Odoo user who processed this charge through the "
             "Clover terminal. Captured from the HTTP request user "
             "BEFORE the controller elevates to sudo, so it reflects "
             "the actual logged-in cashier rather than the system user.",
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

    def _clover_get_external_ref(self):
        """Return a deterministic ≤12-char alphanumeric reference suitable
        for Clover's ``external_reference_id`` field.

        Clover's Ecommerce v1/charges API rejects values longer than 12
        characters and rejects non-alphanumeric formats. Odoo's default
        reference ``tx-YYYYMMDDHHMMSS`` violates both. Strategy:

        1. Strip every non-alphanumeric character from ``self.reference``.
        2. If the stripped string fits in 12 chars, use it verbatim
           (keeps things human-readable in the Clover dashboard).
        3. Otherwise hash the full reference with MD5 and take the
           first 12 hex chars, prefixed with 'cl' to guarantee a stable
           non-numeric leading char.

        The result is stored on the transaction so the inverse lookup
        (webhook → tx) can be done by exact match on
        ``clover_external_ref``.
        """
        self.ensure_one()
        stripped = re.sub(r'[^A-Za-z0-9]', '', self.reference or '')
        if 0 < len(stripped) <= 12:
            return stripped
        digest = hashlib.md5(
            (self.reference or str(self.id)).encode('utf-8')
        ).hexdigest()
        return f"cl{digest[:10]}"

    def _clover_build_line_items(self):
        """Return a list of Clover line-item dicts for this transaction.

        Priority order matches ``_clover_build_description`` so the
        Clover dashboard's Order display and the charge description
        tell the same story:

        1. **Terminal flow** — context ``clover_charge_description``
           supplies the item name (this is what the cashier picked
           from the dropdown), so we emit one line item with that name
           and the full transaction amount.
        2. **Invoice flow** — if invoice_ids exists (set when
           account_payment processes an invoice via Clover), iterate
           the invoice lines and emit one Clover line item per Odoo
           invoice line, preserving names and amounts.
        3. **Sale order flow** — similar to invoices, for sale.order
           lines.
        4. **Fallback** — single generic line item named after the
           reference, full amount.

        Amounts are returned in minor currency units (cents) to match
        what the charges API expects.
        """
        self.ensure_one()

        def _to_minor(amount):
            return payment_utils.to_minor_currency_units(
                amount, self.currency_id
            )

        # 1) Terminal — single item using the cashier-supplied description.
        # Clover's per-item field is `description` (NOT `name`); using
        # `name` would be silently ignored and the line would render as
        # generic "Item 1" in the dashboard.
        ctx_desc = self.env.context.get("clover_charge_description", "")
        if ctx_desc:
            return [{
                "description": ctx_desc[:127] or "Payment",
                "currency": self.currency_id.name.lower(),
                "amount": _to_minor(self.amount),
                "quantity": 1,
            }]

        # 2) Invoice lines (account module adds invoice_ids via account_payment)
        if hasattr(self, "invoice_ids") and self.invoice_ids:
            inv = self.invoice_ids[0]
            product_lines = inv.invoice_line_ids.filtered(
                lambda l: l.display_type == "product"
            )
            if product_lines:
                items = []
                for line in product_lines:
                    items.append({
                        "description": (line.name or line.product_id.display_name
                                 or "Line item")[:127],
                        "currency": self.currency_id.name.lower(),
                        "amount": _to_minor(line.price_total),
                        "quantity": int(line.quantity) if line.quantity else 1,
                    })
                return items

        # 3) Sale order lines (sale module adds sale_order_ids)
        if hasattr(self, "sale_order_ids") and self.sale_order_ids:
            so = self.sale_order_ids[0]
            order_lines = so.order_line.filtered(
                lambda l: not l.display_type
            )
            if order_lines:
                items = []
                for line in order_lines:
                    items.append({
                        "description": (line.name or line.product_id.display_name
                                 or "Line item")[:127],
                        "currency": self.currency_id.name.lower(),
                        "amount": _to_minor(line.price_total),
                        "quantity": int(line.product_uom_qty) if line.product_uom_qty else 1,
                    })
                return items

        # 4) Fallback — single generic item
        return [{
            "description": (self.reference or "Payment")[:127],
            "currency": self.currency_id.name.lower(),
            "amount": _to_minor(self.amount),
            "quantity": 1,
        }]

    def _clover_create_order(self):
        """Create a Clover Order with real line items and return its ID.

        Called from ``_send_payment_request`` before the charge so that
        the Clover dashboard shows the actual item name(s) instead of a
        generic 'Item 1' line auto-generated when only a Charge is
        created.

        Returns the Clover order_id on success, or ``None`` on any
        failure — callers should fall back to charge-only mode in
        that case so a Clover Orders API outage doesn't break payment
        processing.
        """
        self.ensure_one()
        try:
            line_items = self._clover_build_line_items()
            if not line_items:
                return None

            payload = {
                "currency": self.currency_id.name.lower(),
                "items": line_items,
            }
            if self.partner_email:
                payload["email"] = self.partner_email
            # Some Clover Order versions accept a top-level description
            # which surfaces alongside the order in the dashboard. We
            # include it; if the field is ignored, no harm done.
            payload["description"] = self._clover_build_description()

            response = self.provider_id._clover_make_request(
                "POST", "v1/orders", payload=payload,
                idempotency_key=payment_utils.generate_idempotency_key(
                    self, scope="orders"
                ),
            )
            order_id = (response or {}).get("id")
            if order_id:
                _logger.info(
                    "Clover: created order %s for tx %s with %d line items",
                    order_id, self.reference, len(line_items),
                )
                return order_id
            _logger.warning(
                "Clover: order response missing 'id' for tx %s; response=%s",
                self.reference, str(response)[:300],
            )
            return None
        except Exception as e:  # noqa: BLE001
            # Don't fail the charge if order creation fails — Clover
            # will just show a generic line item, which is what we had
            # before this feature was added.
            _logger.warning(
                "Clover: order creation failed for tx %s (%s) — "
                "falling back to charge-only.",
                self.reference, e,
            )
            return None

    def _clover_build_description(self):
        """Build a human-readable description for the Clover charge.

        This string becomes the **"Note"** field on the Clover dashboard's
        Payment detail screen, so we put the most-useful info first:
        item name, then customer, then the Odoo reference as a tail
        identifier. The external_reference_id field carries the same
        reference in a structured form, so the cashier doesn't need
        the long ``tx-YYYYMMDDHHMMSS`` to lead the description.

        Priority:
        1. Terminal — context description (cashier-selected item) first
        2. Invoice — invoice number + line names
        3. Sale order — order number + line names
        4. Fallback — the Odoo transaction reference

        For 1, 2, 3 we append the partner name when available so the
        Payment list at a glance reads "ENF (Elks Nat'l Fnd) — Christopher
        Spataro" instead of an opaque transaction id.
        """
        # 1) Terminal description (staff entered or auto-filled from item)
        ctx_desc = self.env.context.get("clover_charge_description", "")
        if ctx_desc:
            parts = [ctx_desc]
            if self.partner_name:
                parts.append(self.partner_name)
            # Cashier-name marker: Clover's "Employee" column on the
            # Payment screen always shows the API-token owner (no way
            # to override that from the Ecommerce API), so we surface
            # the actual Odoo user in the Note instead. Admins scanning
            # the Clover dashboard see who processed each charge
            # without bouncing to Odoo.
            cashier = (
                self.env.user.name if self.env.user
                and self.env.user.id != self.env.ref('base.user_root').id
                else "Odoo"
            )
            parts.append(f"by {cashier}")
            # Append short ref in brackets so reconciliation still works
            # without it dominating the readable portion.
            parts.append(f"[{self.reference}]")
            return " — ".join(parts)

        # Same cashier-name lookup used by the non-terminal branches
        # below so the Note consistently shows who processed it.
        cashier = (
            self.env.user.name if self.env.user
            and self.env.user.id != self.env.ref('base.user_root').id
            else "Odoo"
        )

        # 2) Invoice lines (account_payment adds invoice_ids)
        if hasattr(self, "invoice_ids") and self.invoice_ids:
            inv = self.invoice_ids[0]
            lines = inv.invoice_line_ids.filtered(
                lambda l: l.display_type == "product"
            )
            label_items = (
                ", ".join(lines.mapped("name")[:10]) if lines else self.reference
            )
            parts = [f"{inv.name}: {label_items}"]
            if self.partner_name:
                parts.append(self.partner_name)
            parts.append(f"by {cashier}")
            return " — ".join(parts)

        # 3) Sale order lines (sale module adds sale_order_ids)
        if hasattr(self, "sale_order_ids") and self.sale_order_ids:
            so = self.sale_order_ids[0]
            lines = so.order_line.filtered(
                lambda l: not l.display_type
            )
            label_items = (
                ", ".join(lines.mapped("name")[:10]) if lines else self.reference
            )
            parts = [f"{so.name}: {label_items}"]
            if self.partner_name:
                parts.append(self.partner_name)
            parts.append(f"by {cashier}")
            return " — ".join(parts)

        # 4) Fallback — just the reference
        return self.reference

    def _send_payment_request(self):
        """Override of `payment` to create a Clover charge.

        All exit paths set a definitive transaction state (done / authorized
        / pending / cancel / error) so the row is always visible in the
        Transaction Log. Unhandled exceptions are caught and converted to
        ``state='error'`` with a descriptive message so a Clover outage,
        network blip, or malformed response never causes a silent rollback
        of the transaction row by the controller.
        """
        if self.provider_code != "clover":
            return super()._send_payment_request()

        # The source token was passed via the controller
        source_token = self.env.context.get("clover_source_token")
        if not source_token:
            self._set_error(_("No card token received from Clover."))
            return

        try:
            amount_minor = payment_utils.to_minor_currency_units(
                self.amount, self.currency_id
            )
            # Build (or reuse) the 12-char Clover external reference.
            # We persist it BEFORE the API call so the row in the
            # Transaction Log carries the same value Clover knows it
            # by, even if the charge fails.
            ext_ref = self._clover_get_external_ref()
            if self.clover_external_ref != ext_ref:
                self.clover_external_ref = ext_ref

            # Clover has TWO charge paths with different capabilities:
            #
            #   POST /v1/charges                  → supports capture=false
            #                                       (pre-auth), but Clover
            #                                       auto-creates a stub
            #                                       order with a generic
            #                                       "Item 1" line.
            #
            #   POST /v1/orders/{id}/pay          → uses real line items
            #                                       from a pre-created
            #                                       order, but rejects
            #                                       capture=false with
            #                                       "Create pre-auth for
            #                                       an Order feature is
            #                                       not available".
            #
            # Choose the path based on whether the provider is in
            # manual-capture mode. Manual capture ⇒ legacy /v1/charges
            # so cashiers can auth-now-and-capture-later. Auto capture
            # ⇒ Orders + /pay so the Clover dashboard displays the real
            # line items.
            use_orders_flow = not self.provider_id.capture_manually
            order_id = None
            if use_orders_flow:
                order_id = self._clover_create_order()
                if order_id and self.clover_order_id != order_id:
                    self.clover_order_id = order_id

            if order_id:
                # Pay-the-order endpoint. `ecomind: "ecom"` is the
                # standard ecommerce indicator required by /pay.
                #
                # Note re: the Payment "Note" field — when charges go
                # through the older /v1/charges endpoint, Clover uses
                # the body's `description` as the Payment Note. The
                # /pay endpoint does NOT auto-populate Note from the
                # parent Order's description, so we re-send our
                # description (and a duplicate `note` field as belt-
                # and-suspenders) here so the Payment screen still
                # carries the "item — customer — by cashier — [ref]"
                # information when Daniel scans the Clover dashboard.
                description = self._clover_build_description()
                endpoint = f"v1/orders/{order_id}/pay"
                payload = {
                    "ecomind": "ecom",
                    "amount": amount_minor,
                    "currency": self.currency_id.name.lower(),
                    "source": source_token,
                    "external_reference_id": ext_ref,
                    "description": description,
                    "note": description,
                    # Capture flag is intentionally OMITTED here:
                    # /pay always immediate-captures, and sending
                    # capture=false returns 400.
                }
            else:
                # Manual-capture mode (or order creation failed):
                # use the orderless /v1/charges endpoint. Supports
                # capture=false but the Clover dashboard will show a
                # generic "Item 1" line on the auto-created order.
                endpoint = "v1/charges"
                payload = {
                    "amount": amount_minor,
                    "currency": self.currency_id.name.lower(),
                    "source": source_token,
                    "description": self._clover_build_description(),
                    "external_reference_id": ext_ref,
                    "capture": not self.provider_id.capture_manually,
                }
                if self.partner_email:
                    payload["receipt_email"] = self.partner_email
        except Exception as e:  # noqa: BLE001
            _logger.exception(
                "Clover: failed to build charge payload for %s", self.reference
            )
            self._set_error(_("Could not build Clover charge payload: %s") % e)
            return

        try:
            response = self.provider_id._clover_make_request(
                "POST", endpoint, payload=payload,
                idempotency_key=payment_utils.generate_idempotency_key(
                    self, scope="charges"
                ),
            )
        except ValidationError as e:
            # Expected error class raised by _send_api_request when Clover
            # returns a 4xx/5xx — message already user-friendly.
            self._set_error(str(e))
            return
        except Exception as e:  # noqa: BLE001
            # Network errors, JSON decode errors, etc.
            _logger.exception(
                "Clover: unexpected error calling charges API for %s",
                self.reference,
            )
            self._set_error(_("Could not reach Clover: %s") % e)
            return

        # Build payment_data and process — wrap so a malformed Clover
        # response or a bug in _apply_updates also leaves a tidy trail.
        try:
            self._process("clover", {
                "reference": self.reference,
                "charge": response,
            })
        except Exception as e:  # noqa: BLE001
            _logger.exception(
                "Clover: error processing charge response for %s",
                self.reference,
            )
            self._set_error(_("Error processing Clover response: %s") % e)

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
        """Override of `payment` to find the transaction by Clover data.

        Clover knows transactions by the 12-char alphanumeric value we
        sent in ``external_reference_id`` (stored on each tx as
        ``clover_external_ref``). Webhooks return that value back to us,
        so we look up first by ``clover_external_ref``. We fall back to
        the full Odoo reference for backward compatibility with rows
        created before this short-ref scheme existed (and so the inline
        form's return controller, which still passes the full
        ``self.reference``, continues to work).
        """
        if provider_code != "clover":
            return super()._search_by_reference(provider_code, payment_data)

        reference = payment_data.get("reference")
        tx = self
        if reference:
            tx = self.search([
                ("clover_external_ref", "=", reference),
                ("provider_code", "=", "clover"),
            ], limit=1)
            if not tx:
                tx = self.search([
                    ("reference", "=", reference),
                    ("provider_code", "=", "clover"),
                ], limit=1)
        else:
            _logger.warning("Received Clover data with missing reference")
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
