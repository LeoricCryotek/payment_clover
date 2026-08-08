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

from odoo import _, http
from odoo.http import request

_logger = logging.getLogger(__name__)


def _verify_clover_signature(raw_body, signature_header, secret):
    """Validate a Clover webhook HMAC-SHA256 signature.

    Clover signs webhook deliveries with the merchant's webhook secret
    using HMAC-SHA256 over the raw request body. The hex digest is sent
    in the ``X-Clover-Auth`` header.

    :param bytes raw_body: The raw HTTP request body.
    :param str signature_header: Value of the ``X-Clover-Auth`` header.
    :param str secret: The webhook secret configured on the provider.
    :return: True iff the signature matches.
    :rtype: bool
    """
    if not secret or not signature_header:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


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
        changed but we must fetch the details ourselves. When the
        provider has a webhook secret configured we validate the
        ``X-Clover-Auth`` HMAC signature and reject mismatches; if no
        secret is configured we log a warning and accept the payload
        (back-compat path — strongly recommended to configure a secret).
        """
        raw_body = request.httprequest.get_data() or b""
        signature_header = request.httprequest.headers.get("X-Clover-Auth", "")

        try:
            data = json.loads(raw_body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            _logger.warning("Clover webhook: could not parse JSON body")
            return request.make_json_response(
                {"status": "error", "message": "bad json"}, status=400
            )

        _logger.info("Clover webhook received: %s", json.dumps(data)[:500])

        # Process each merchant's events
        merchants = data.get("merchants", {}) if isinstance(data, dict) else {}
        for merchant_id, events in merchants.items():
            # Resolve the provider once per merchant so we can check
            # the webhook secret before consuming any events.
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

            secret = provider_sudo.clover_webhook_secret or ""
            if secret:
                if not _verify_clover_signature(
                    raw_body, signature_header, secret
                ):
                    _logger.warning(
                        "Clover webhook: invalid signature for merchant %s",
                        merchant_id,
                    )
                    return request.make_json_response(
                        {"status": "error", "message": "invalid signature"},
                        status=403,
                    )
            else:
                _logger.warning(
                    "Clover webhook: no webhook secret configured for "
                    "merchant %s — accepting unverified payload. Configure "
                    "a secret on the Clover provider to enforce signature "
                    "verification.",
                    merchant_id,
                )

            for event in events or []:
                event_type = event.get("type", "")
                object_id = event.get("objectId", "")
                if not object_id:
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
    # Terminal: item list for product picker
    # ------------------------------------------------------------------

    @http.route(
        "/payment/clover/terminal/items",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def terminal_items(self, provider_id, **kwargs):
        """Return Clover items for the terminal product picker."""
        items = (
            request.env["clover.item"]
            .sudo()
            .search_read(
                [
                    ("provider_id", "=", int(provider_id)),
                    ("active", "=", True),
                    ("hidden", "=", False),
                ],
                ["id", "name", "price", "price_type", "sku",
                 "category_name", "clover_item_id"],
                order="category_name, name",
            )
        )
        return {"items": items}

    # ------------------------------------------------------------------
    # Terminal: guest / walk-in partner
    # ------------------------------------------------------------------

    @http.route(
        "/payment/clover/terminal/guest_partner",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def terminal_guest_partner(self, **kwargs):
        """Find or create a 'Guest / Walk-in' partner for anonymous charges."""
        Partner = request.env["res.partner"].sudo()
        guest = Partner.search([
            ("name", "=", "Guest / Walk-in"),
            ("is_company", "=", False),
            ("active", "=", True),
        ], limit=1)
        if not guest:
            guest = Partner.create({
                "name": "Guest / Walk-in",
                "is_company": False,
                "customer_rank": 1,
                "comment": "Auto-created for anonymous Clover terminal payments.",
            })
        return {"partner_id": guest.id}

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
                         rv_registration_id=None, **kwargs):
        """Process a payment from the staff terminal.

        Creates a payment.transaction and immediately charges via Clover.

        ``rv_registration_id`` is an optional generic link: when supplied
        (and the field exists — i.e. the elksrvparking module is installed)
        it is stored on the transaction so completion can be recorded back
        onto the RV registration. Ignored otherwise, keeping this module
        installable on its own.

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

        # payment.transaction.payment_method_id is required in Odoo 19.
        # Resolution order:
        #   1. Active 'card' method already linked to this provider.
        #   2. Any other active method already linked to this provider.
        #   3. Global 'card' method (active=True) — link to provider on use.
        #   4. AUTO-HEAL: global 'card' method that exists but is inactive
        #      — activate it AND link to provider, then use it. This is
        #      necessary because Odoo 19's payment._setup_provider only
        #      copies the provider record across companies; it does NOT
        #      activate+link the default methods, so a fresh install
        #      leaves the link empty until an admin clicks "Enable
        #      Payment Methods" in the Configuration tab. The auto-heal
        #      makes the very first charge attempt fix this silently.
        payment_method = provider_sudo.payment_method_ids.filtered(
            lambda m: m.code == "card" and m.active
        )[:1]
        if not payment_method:
            payment_method = provider_sudo.payment_method_ids.filtered("active")[:1]
        if not payment_method:
            payment_method = request.env["payment.method"].sudo().search(
                [("code", "=", "card"), ("active", "=", True)],
                limit=1,
            )
            if payment_method:
                provider_sudo.sudo().write({
                    "payment_method_ids": [(4, payment_method.id)],
                })
                _logger.info(
                    "Clover provider %s self-healed: linked existing "
                    "active 'card' payment.method (id=%s).",
                    provider_sudo.id, payment_method.id,
                )
        if not payment_method:
            # Auto-heal — Card method exists in DB but ships inactive.
            card_method = request.env["payment.method"].sudo().search(
                [("code", "=", "card")], limit=1,
            )
            if card_method:
                card_method.sudo().write({"active": True})
                provider_sudo.sudo().write({
                    "payment_method_ids": [(4, card_method.id)],
                })
                payment_method = card_method
                _logger.warning(
                    "Clover provider %s self-healed: activated the "
                    "inactive 'card' payment.method (id=%s) and linked "
                    "it. Configuration tab → 'Enable Payment Methods' "
                    "would have done the same.",
                    provider_sudo.id, card_method.id,
                )
        if not payment_method:
            # We've exhausted every avenue — the `payment` module's
            # default methods are missing entirely.
            _logger.error(
                "Clover provider %s: no 'card' payment.method exists in "
                "the database. The `payment` module data file did not "
                "seed it. Try reinstalling the `payment` module.",
                provider_sudo.id,
            )
            return {
                "status": "error",
                "message": _(
                    "No 'card' payment method exists in the database. "
                    "An administrator may need to reinstall the base "
                    "Payment module."
                ),
            }

        # Capture the actual logged-in cashier BEFORE we elevate to
        # sudo. After .sudo(), `env.user` flips to the system user and
        # Odoo's auto-set `create_uid` would record that — useless for
        # bookkeeping. Stash the real uid here and pass it explicitly
        # into the create vals as `clover_cashier_id`.
        cashier_uid = request.env.user.id

        # Optional link back to an RV registration (elksrvparking). Only
        # set when the field is present so this module stays standalone.
        extra_vals = {}
        TxModel = request.env["payment.transaction"]
        if rv_registration_id and "rv_registration_id" in TxModel._fields:
            try:
                extra_vals["rv_registration_id"] = int(rv_registration_id)
            except (ValueError, TypeError):
                pass

        # Create the transaction record BEFORE attempting the charge so
        # that even a hard failure produces a payment.transaction row in
        # the Transaction Log. The cursor will commit at the end of this
        # request as long as we don't re-raise. Wrap the create() itself
        # so any unexpected validation failure (e.g. a future required
        # field added by another module) returns a clean error to the
        # cashier instead of an HTTP 500 / RPC error popup.
        try:
            tx_sudo = (
                request.env["payment.transaction"]
                .sudo()
                .create({
                    "provider_id": provider_sudo.id,
                    "payment_method_id": payment_method.id,
                    "reference": request.env["payment.transaction"]
                        .sudo()
                        ._compute_reference("clover"),
                    "amount": amount,
                    "currency_id": currency.id,
                    "partner_id": partner.id,
                    "operation": "online_direct",
                    "clover_cashier_id": cashier_uid,
                    # Persist the cashier's free-text description on
                    # the Odoo record. Clover's Ecommerce API doesn't
                    # surface this anywhere on the dashboard, so the
                    # Odoo Transaction Log is the canonical place to
                    # see what was purchased / what the cashier noted.
                    "clover_description": (description or "")[:255],
                    **extra_vals,
                })
            )
        except Exception as e:  # noqa: BLE001
            _logger.exception(
                "Clover: failed to create payment.transaction in terminal_process"
            )
            return {
                "status": "error",
                "message": _("Could not create payment record: %s") % e,
            }

        tx_sudo = tx_sudo.with_context(
            clover_source_token=clover_token,
            clover_charge_description=description or "",
        )

        # Charge — any unexpected exception is caught so the request
        # completes normally and the transaction row persists with
        # state='error'. Without this catch, Odoo rolls back the whole
        # request (including the create() above) and failures vanish
        # from the Transaction Log.
        try:
            tx_sudo._send_payment_request()
        except Exception as e:  # noqa: BLE001 — intentional broad catch
            _logger.exception(
                "Clover terminal charge raised an unhandled exception "
                "for transaction %s", tx_sudo.reference,
            )
            try:
                tx_sudo._set_error(
                    (_("Unexpected error while charging Clover: %s") %
                     str(e))[:1000]
                )
            except Exception:  # noqa: BLE001
                # Last-resort fallback if even _set_error blows up — write
                # the state directly so the row at least shows in the log.
                tx_sudo.sudo().write({
                    "state": "error",
                    "state_message": str(e)[:1000],
                })

        # If this charge is linked to another document (e.g. an RV
        # registration) run post-processing now so the write-back happens
        # immediately rather than waiting for the cron. Idempotent.
        if extra_vals.get("rv_registration_id") and tx_sudo.state == "done":
            try:
                tx_sudo._post_process()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Clover: post-process after linked terminal charge failed "
                    "for %s", tx_sudo.reference,
                )

        return {
            "status": "ok" if tx_sudo.state in ("done", "authorized") else "error",
            "state": tx_sudo.state,
            "reference": tx_sudo.reference,
            "provider_reference": tx_sudo.provider_reference or "",
            "message": tx_sudo.state_message or "",
        }
