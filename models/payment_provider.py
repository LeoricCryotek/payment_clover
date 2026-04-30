# -*- coding: utf-8 -*-
# Part of Elks Lodge Odoo Modules. See LICENSE file for full copyright and licensing details.

import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment_clover import const


_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    """Extend payment.provider with Clover-specific configuration.

    Adds API credentials, merchant ID, and environment settings needed
    to process payments through Clover's REST API and hosted iframe SDK.
    Restricted to USD transactions for US-based Elks Lodges.
    """
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[("clover", "Clover")],
        ondelete={"clover": "set default"},
    )

    clover_api_key = fields.Char(
        string="API Key (Bearer Token)",
        help="The Clover merchant API token used for server-to-server "
             "requests (charges, refunds, etc.). Get this from your "
             "Clover merchant dashboard under API Tokens.",
        copy=False,
        groups="base.group_system",
    )
    clover_pakms_key = fields.Char(
        string="Public Tokenizer Key (PAKMS)",
        help="The public API key used by the Clover iframe SDK to "
             "tokenize card details on the client side. Obtained from "
             "the Clover PAKMS endpoint.",
        copy=False,
    )
    clover_merchant_id = fields.Char(
        string="Merchant ID",
        help="Your Clover Merchant ID, found in your Clover dashboard "
             "under Account & Setup.",
        copy=False,
    )
    clover_item_ids = fields.One2many(
        "clover.item", "provider_id",
        string="Clover Items",
    )

    # ------------------------------------------------------------------
    # Constraints — only enforce credentials when fully enabled
    # ------------------------------------------------------------------

    @api.constrains("state", "code", "clover_api_key", "clover_pakms_key",
                     "clover_merchant_id")
    def _check_clover_credentials(self):
        """Ensure Clover credentials are filled before enabling.

        You can save the provider in Disabled or Test mode without
        credentials to configure other settings first.  Credentials
        are only required when the state is set to 'enabled'.
        """
        for provider in self:
            if provider.code != "clover":
                continue
            if provider.state == "enabled":
                missing = []
                if not provider.clover_api_key:
                    missing.append("API Key (Bearer Token)")
                if not provider.clover_pakms_key:
                    missing.append("Public Tokenizer Key (PAKMS)")
                if not provider.clover_merchant_id:
                    missing.append("Merchant ID")
                if missing:
                    raise ValidationError(_(
                        "Before enabling the Clover provider, please fill "
                        "in the following credentials: %s\n\n"
                        "Tip: Set the State to 'Disabled' or 'Test Mode' "
                        "first, fill in your credentials, then enable.",
                        ", ".join(missing),
                    ))

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------

    def _compute_feature_support_fields(self):
        """Override of `payment` to enable Clover-supported features."""
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == "clover").update({
            "support_manual_capture": "full_only",
            "support_refund": "partial",
            "support_tokenization": False,
            "support_express_checkout": False,
        })

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------

    def _get_default_payment_method_codes(self):
        """Override of `payment` to return the default payment method codes."""
        self.ensure_one()
        if self.code != "clover":
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    def _get_supported_currencies(self):
        """Override of `payment` to return only USD for Elks lodges.

        Clover supports USD, CAD, GBP, EUR but Elks lodges operate
        exclusively in the United States, so we default to USD only.
        """
        supported_currencies = super()._get_supported_currencies()
        if self.code != "clover":
            return supported_currencies
        return supported_currencies.filtered(
            lambda c: c.name == "USD"
        )

    # ------------------------------------------------------------------
    # Business helpers
    # ------------------------------------------------------------------

    def _clover_get_api_url(self, service="ecommerce"):
        """Return the API base URL for the current environment.

        :param str service: 'ecommerce', 'platform', 'tokenizer', or 'iframe_sdk'
        :return: The base URL string
        :rtype: str
        """
        self.ensure_one()
        env_key = "sandbox" if self.state == "test" else "production"
        return const.API_URLS[env_key][service]

    def _clover_get_inline_form_values(self):
        """Return the values needed to render the Clover inline payment form.

        :return: dict with pakms_key and sdk_url
        :rtype: dict
        """
        self.ensure_one()
        return {
            "pakms_key": self.sudo().clover_pakms_key,
            "merchant_id": self.sudo().clover_merchant_id,
            "sdk_url": self._clover_get_api_url("iframe_sdk"),
        }

    # ------------------------------------------------------------------
    # Request helpers  (override base payment.provider helpers)
    # ------------------------------------------------------------------

    def _build_request_url(self, endpoint, **kwargs):
        """Override of `payment` to build the Clover API URL."""
        if self.code != "clover":
            return super()._build_request_url(endpoint, **kwargs)
        base = self._clover_get_api_url(
            kwargs.get("service", "ecommerce")
        )
        return f"{base}/{endpoint}"

    def _build_request_headers(self, method, *args, **kwargs):
        """Override of `payment` to build the Clover request headers."""
        if self.code != "clover":
            return super()._build_request_headers(method, *args, **kwargs)
        headers = {
            "Authorization": f"Bearer {self.sudo().clover_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        idempotency_key = kwargs.get("idempotency_key")
        if method == "POST" and idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _parse_response_error(self, response):
        """Override of `payment` to extract Clover error messages."""
        if self.code != "clover":
            return super()._parse_response_error(response)
        try:
            data = response.json()
            error = data.get("error", {})
            return error.get("message", "") or str(data)
        except Exception:
            return response.text or str(response.status_code)

    # ------------------------------------------------------------------
    # Convenience: direct Clover API call
    # ------------------------------------------------------------------

    def _clover_make_request(self, method, endpoint, payload=None, **kwargs):
        """Make a request to the Clover Ecommerce API.

        Wraps ``_send_api_request`` with Clover-specific defaults.

        :param str method: HTTP method (GET, POST)
        :param str endpoint: e.g. 'v1/charges'
        :param dict payload: JSON body
        :return: Parsed response dict
        :rtype: dict
        :raise ValidationError: On HTTP or connection error
        """
        self.ensure_one()
        return self._send_api_request(
            method,
            endpoint,
            json=payload,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Clover inventory sync
    # ------------------------------------------------------------------

    def _clover_platform_request(self, method, path):
        """Make a direct request to the Clover Platform API.

        The platform API lives at a different base URL than the ecommerce
        API and uses the same Bearer token.

        :param str method: HTTP method
        :param str path: e.g. '/v3/merchants/{mId}/items'
        :return: Parsed JSON dict
        """
        self.ensure_one()
        base = self._clover_get_api_url("platform")
        url = f"{base}{path}"
        headers = {
            "Authorization": f"Bearer {self.sudo().clover_api_key}",
            "Accept": "application/json",
        }
        _logger.info("Clover platform request: %s %s", method, url)
        resp = requests.request(method, url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def action_sync_clover_items(self):
        """Fetch all items from Clover and create/update clover.item records.

        Called from the "Sync Items" button on the provider form.
        """
        self.ensure_one()
        if self.code != "clover":
            return
        if not self.clover_merchant_id or not self.clover_api_key:
            raise ValidationError(_(
                "Please configure the Merchant ID and API Key before syncing."
            ))

        mId = self.clover_merchant_id
        CloverItem = self.env["clover.item"].sudo()
        now = fields.Datetime.now()

        # Fetch items (paginated — Clover returns up to 100 per page)
        offset = 0
        limit = 100
        total_created = 0
        total_updated = 0

        while True:
            path = (
                f"/v3/merchants/{mId}/items"
                f"?limit={limit}&offset={offset}"
                f"&expand=categories"
            )
            try:
                data = self._clover_platform_request("GET", path)
            except requests.HTTPError as e:
                raise ValidationError(_(
                    "Failed to fetch items from Clover: %s", str(e)
                ))
            except requests.ConnectionError:
                raise ValidationError(_(
                    "Could not connect to Clover. Check your internet "
                    "connection and API credentials."
                ))

            elements = data.get("elements", [])
            if not elements:
                break

            for item in elements:
                clover_id = item.get("id", "")
                if not clover_id:
                    continue

                # Extract category name from expanded data
                cat_name = ""
                categories = item.get("categories", {})
                cat_elements = categories.get("elements", [])
                if cat_elements:
                    cat_name = cat_elements[0].get("name", "")

                # Clover prices are in cents
                price_cents = item.get("price", 0) or 0
                price_dollars = price_cents / 100.0

                vals = {
                    "name": item.get("name", "Unknown Item"),
                    "price": price_dollars,
                    "price_type": item.get("priceType", "FIXED"),
                    "sku": item.get("sku", "") or "",
                    "category_name": cat_name,
                    "hidden": item.get("hidden", False),
                    "active": not item.get("hidden", False),
                    "last_synced": now,
                }

                # Include inactive records so re-appearing items
                # reactivate instead of hitting the unique constraint.
                existing = CloverItem.with_context(active_test=False).search([
                    ("provider_id", "=", self.id),
                    ("clover_item_id", "=", clover_id),
                ], limit=1)

                if existing:
                    existing.write(vals)
                    total_updated += 1
                    # Keep the linked Odoo product in sync
                    if existing.product_id:
                        existing.product_id.sudo().write({
                            "name": vals["name"],
                            "list_price": vals["price"],
                            "default_code": vals["sku"] or False,
                        })
                else:
                    vals.update({
                        "provider_id": self.id,
                        "clover_item_id": clover_id,
                    })
                    record = CloverItem.create(vals)
                    total_created += 1

                    # Auto-create linked Odoo product
                    if not record.product_id:
                        product = self.env["product.product"].sudo().create({
                            "name": record.name,
                            "list_price": record.price,
                            "type": "service",
                            "sale_ok": True,
                            "purchase_ok": False,
                            "default_code": record.sku or False,
                        })
                        record.product_id = product.id

            offset += limit
            # Check if there are more pages
            if len(elements) < limit:
                break

        # Mark items not seen in this sync as inactive
        stale = CloverItem.search([
            ("provider_id", "=", self.id),
            ("last_synced", "<", now),
            ("active", "=", True),
        ])
        if stale:
            stale.write({"active": False})

        msg = _(
            "Clover item sync complete: %(created)s created, "
            "%(updated)s updated, %(stale)s deactivated.",
            created=total_created,
            updated=total_updated,
            stale=len(stale),
        )
        _logger.info(msg)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Clover Sync"),
                "message": msg,
                "type": "success",
                "sticky": False,
            },
        }
