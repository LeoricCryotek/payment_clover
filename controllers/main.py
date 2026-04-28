# -*- coding: utf-8 -*-
# Part of Elks Lodge Odoo Modules. See LICENSE file for full copyright and licensing details.

"""Clover payment controller.

Handles:
  1. The return from the inline payment form (receives the Clover token
     and triggers the charge).
  2. Webhook notifications from Clover.
  3. The standalone payment terminal for staff.
"""
import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class CloverController(http.Controller):
    """HTTP endpoints for Clover payment processing.

    Provides three routes:
    - /payment/clover/return — inline form callback (tokenize → charge)
    - /payment/clover/webhook — Clover webhook notifications
    - /payment/clover/terminal/process — staff-facing terminal charges
    """

    _return_url = "/payment/clover/return"
    _webhook_url = "/payment/clover/webhook"
    _terminal_process_url = "/payment/clover/terminal/process"

    # ------------------------------------------------------------------
    # Inline form return — receives token from JS, creates charge
    # ------------------------------------------------------------------

    @http.route(
        _return_url,
        type="jsonrpc",
        auth="public",
        methods=["POST"],
    )
    def clover_return(self, reference, clover_token, **kwargs):
        """Called by the inline form JS after Clover tokenises the card.

        :param str reference: The Odoo payment.transaction reference
        :param str clover_token: The Clover source token (clv_...)
        :return: dict with 'status' key
        """
        tx_sudo = (
            request.env["payment.transaction"]
            .sudo()
            ._search_by_reference("clover", {"reference": reference})
        )
        if not tx_sudo:
            return {"status": "error", "message": "Transaction not found."}

        # Pass the token via context so _send_payment_request can use it
        tx_sudo = tx_sudo.with_context(clover_source_token=clover_token)
        tx_sudo._send_payment_request()

        return {
            "status": "ok" if tx_sudo.state in ("done", "authorized", "pending") else "error",
            "state": tx_sudo.state,
            "message": tx_sudo.state_message or "",
        }

    # ------------------------------------------------------------------
    # Webhook — Clover pushes event notifications
    # ------------------------------------------------------------------

    @http.route(
        _webhook_url,
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def clover_webhook(self):
        """Receive and process Clover webhook notifications.

        Clover webhooks are *thin* notifications — they tell us something
        changed but we must fetch the details ourselves.
        """
        try:
            data = request.get_json_data()
        except Exception:
            _logger.warning("Clover webhook: could not parse JSON body")
            return request.make_json_response(
                {"status": "error"}, status=400
            )

        _logger.info("Clover webhook received: %s", json.dumps(data)[:500])

        # Process each merchant's events
        merchants = data.get("merchants", {})
        for merchant_id, events in merchants.items():
            for event in events:
                event_type = event.get("type", "")
                object_id = event.get("objectId", "")

                if not object_id:
                    continue

                # Find the provider for this merchant
                provider_sudo = (
                    request.env["payment.provider"]
                    .sudo()
                    .search([
                        ("code", "=", "clover"),
                        ("clover_merchant_id", "=", merchant_id),
                        ("state", "in", ("enabled", "test")),
                    ], limit=1)
                )
                if not provider_sudo:
                    _logger.warning(
                        "Clover webhook: no provider for merchant %s",
                        merchant_id,
                    )
                    continue

                self._process_webhook_event(
                    provider_sudo, event_type, object_id
                )

        return request.make_json_response({"status": "ok"})

    def _process_webhook_event(self, provider_sudo, event_type, object_id):
        """Fetch the charge/refund from Clover and update the transaction.

        :param payment.provider provider_sudo: The sudoed provider
        :param str event_type: e.g. 'CHARGE', 'REFUND'
        :param str object_id: The Clover object ID
        """
        try:
            if event_type in ("CHARGE",):
                charge = provider_sudo._clover_make_request(
                    "GET", f"v1/charges/{object_id}"
                )
                ext_ref = charge.get("external_reference_id", "")
                if ext_ref:
                    tx_sudo = (
                        request.env["payment.transaction"]
                        .sudo()
                        ._search_by_reference(
                            "clover", {"reference": ext_ref}
                        )
                    )
                    if tx_sudo and tx_sudo.state not in ("done", "cancel", "error"):
                        tx_sudo._process("clover", {
                            "reference": ext_ref,
                            "charge": charge,
                        })

            elif event_type in ("REFUND",):
                # Refund objects are nested under a charge
                # We'd need the charge ID to fetch it; log for now
                _logger.info(
                    "Clover refund webhook for object %s — "
                    "manual reconciliation may be needed.",
                    object_id,
                )

        except Exception:
            _logger.exception(
                "Error processing Clover webhook event %s/%s",
                event_type, object_id,
            )

    # ------------------------------------------------------------------
    # Standalone payment terminal (staff-facing)
    # ------------------------------------------------------------------

    @http.route(
        _terminal_process_url,
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def terminal_process(self, provider_id, amount, currency_id,
                         partner_id, clover_token=None, description="",
                         **kwargs):
        """Process a payment from the staff terminal.

        Creates a payment.transaction and immediately charges via Clover.

        :return: dict with transaction details
        """
        if not clover_token:
            return {
                "status": "error",
                "message": "No card token received. Please re-enter card details.",
            }

        provider_sudo = (
            request.env["payment.provider"]
            .sudo()
            .browse(int(provider_id))
        )
        if not provider_sudo.exists() or provider_sudo.code != "clover":
            return {"status": "error", "message": "Invalid Clover provider."}

        currency = request.env["res.currency"].browse(int(currency_id))
        partner = request.env["res.partner"].browse(int(partner_id))

        if not currency.exists() or not partner.exists():
            return {"status": "error", "message": "Invalid currency or partner."}

        # Create a transaction
        tx_sudo = (
            request.env["payment.transaction"]
            .sudo()
            .create({
                "provider_id": provider_sudo.id,
                "reference": request.env["payment.transaction"]
                    .sudo()
                    ._compute_reference("clover"),
                "amount": amount,
                "currency_id": currency.id,
                "partner_id": partner.id,
                "operation": "online_direct",
            })
        )

        # Charge immediately
        tx_sudo = tx_sudo.with_context(clover_source_token=clover_token)
        tx_sudo._send_payment_request()

        return {
            "status": "ok" if tx_sudo.state in ("done", "authorized") else "error",
            "state": tx_sudo.state,
            "reference": tx_sudo.reference,
            "provider_reference": tx_sudo.provider_reference or "",
            "message": tx_sudo.state_message or "",
        }
