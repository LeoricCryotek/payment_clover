# -*- coding: utf-8 -*-
# Part of Elks Lodge Odoo Modules. See LICENSE file for full copyright and licensing details.

import logging

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
