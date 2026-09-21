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
        string="Ecommerce Private Key",
        help="The Clover Ecommerce API PRIVATE token, used for "
             "server-to-server charge/refund/capture calls against "
             "scl.clover.com (Ecommerce service). Get this from your "
             "Clover Merchant Dashboard → Settings → Ecommerce → "
             "Ecommerce API tokens → 'Clover eComm Iframe' → "
             "click the eye icon next to PRIVATE token and copy. "
             "Distinct from the Platform REST API token below.",
        copy=False,
        groups="base.group_system",
    )
    clover_platform_api_key = fields.Char(
        string="Platform REST API Token",
        help="The Clover Platform (REST) API token, used only for "
             "item/inventory sync against api.clover.com (Platform "
             "service). Get this from your Clover Merchant Dashboard "
             "→ Settings → API Tokens, with Inventory READ permission "
             "checked at minimum. Leave blank to fall back to the "
             "Ecommerce Private Key (which will only work if you have "
             "a single token authorised for both services).",
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
    clover_webhook_secret = fields.Char(
        string="Webhook Secret",
        help="The HMAC secret used to sign webhook deliveries from Clover. "
             "When set, incoming webhooks must include a matching "
             "X-Clover-Auth signature header or they are rejected with HTTP "
             "403. Leave blank to accept all webhooks (NOT recommended in "
             "production).",
        copy=False,
        groups="base.group_system",
    )
    clover_item_ids = fields.One2many(
        "clover.item", "provider_id",
        string="Clover Items",
    )

    # -- Sync engine settings (all default OFF; opt-in per feature) ------
    clover_sync_sales = fields.Boolean(
        string="Sync Clover Sales",
        default=False,
        help="Master switch. When ON, the nightly cron and the "
             "on-demand Sync Now button pull Clover POS orders, "
             "payments, tips and employees into mirror records "
             "(clover.sale, clover.tip.entry, clover.employee) so "
             "Odoo reports can see them. Purely read-side — does not "
             "post journal entries or trigger any emails.",
    )
    clover_sync_item_costs = fields.Boolean(
        string="Sync Item Costs to Products",
        default=False,
        help="When ON, item sync writes each Clover item's 'cost' "
             "field to the linked Odoo product's Cost (standard_price). "
             "Leave OFF if your product costs are managed elsewhere; "
             "the sync will still record the cost on the clover.item "
             "record for reporting.",
    )
    clover_auto_create_sale_orders = fields.Boolean(
        string="Create sale.order Records from Clover Sales",
        default=False,
        help="When ON, the sales sync also creates a matching "
             "sale.order (with state='sale') for each Clover POS "
             "order, so Odoo's native sales reports include Clover "
             "revenue. Silent — no order-confirmation emails are "
             "sent. Leave OFF if reconciliation happens in "
             "QuickBooks Online and you only want mirror records "
             "for reporting.",
    )
    clover_last_sales_sync_at = fields.Datetime(
        string="Last Sales Sync",
        readonly=True,
        help="UTC timestamp of the most recent successful sales "
             "pull. Used as the 'modifiedTime>=' filter on the next "
             "run so we only pull deltas.",
    )
    clover_last_employee_sync_at = fields.Datetime(
        string="Last Employee Sync",
        readonly=True,
    )
    clover_auto_create_partners = fields.Boolean(
        string="Auto-Create Contacts for Unmatched Clover Customers",
        default=False,
        help="When ON, sales sync creates a res.partner (silent — no "
             "welcome / portal / reset-password emails) for any "
             "Clover customer whose phone or email doesn't already "
             "match a contact in Odoo. When OFF (default), unmatched "
             "customers are stored as name/email/phone strings on the "
             "clover.sale record only — no res.partner side-effects.",
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
        # Prefer the Platform REST token for api.clover.com calls; fall
        # back to the Ecommerce private key only when no Platform token
        # has been configured, in case a user has a single token
        # authorised for both services.
        platform_token = (
            self.sudo().clover_platform_api_key
            or self.sudo().clover_api_key
        )
        headers = {
            "Authorization": f"Bearer {platform_token}",
            "Accept": "application/json",
        }
        _logger.info("Clover platform request: %s %s", method, url)
        resp = requests.request(method, url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Silence guarantee
    # ------------------------------------------------------------------

    @api.model
    def _clover_silent_context(self):
        """Return a context dict that suppresses email/notification
        side-effects for records created or written during Clover
        sync.

        **Scoped to the sync itself only.** Every sync method calls
        `.with_context(**this dict)` on the models it touches, so the
        flags apply exclusively to the records the sync creates or
        writes during that call. They do NOT change any global
        settings. As soon as the sync returns, subsequent code —
        including a user manually clicking "Send Invoice by Email" or
        confirming a sale.order in the normal UI — behaves exactly as
        it does today. Invoice emails, order-confirmation emails,
        payment receipts triggered by humans are unaffected.

        Used by every sync path (employees, sales, tips, sale.orders,
        partners). Consolidated here so silence is auditable in one
        place — if any future email leaks, the fix is either in this
        dict or in the specific hook being called.

        Suppressed for records touched during sync:
        * mail.thread follower auto-subscription (mail_create_nosubscribe)
        * mail.thread creation log message (mail_create_nolog)
        * mail.thread field tracking on write (tracking_disable, mail_notrack)
        * portal welcome / reset-password emails (no_reset_password)
        * Attachment auto-delete cleanup jobs (mail_auto_delete)
        """
        return {
            "tracking_disable": True,
            "mail_notrack": True,
            "mail_create_nolog": True,
            "mail_create_nosubscribe": True,
            "mail_auto_delete": False,
            "no_reset_password": True,
            "clover_sync_running": True,
        }

    # ------------------------------------------------------------------
    # Customer resolution
    # ------------------------------------------------------------------

    @api.model
    def _clover_normalize_phone(self, phone):
        """Return digit-only version of a phone number for matching.

        Clover stores phone numbers as +1XXXXXXXXXX (US) but users
        may have entered them in Odoo as (208) 743-5591, 208.743.5591,
        etc. We strip everything non-digit and compare the last 10
        digits (US) so 208-743-5591 == +12087435591.
        """
        if not phone:
            return ""
        digits = "".join(c for c in str(phone) if c.isdigit())
        return digits[-10:] if len(digits) >= 10 else digits

    def _clover_resolve_partner(self, customer_dict, dedup_cache=None,
                                 dry_run=False):
        """Find (or optionally create) the res.partner for a Clover customer.

        Match priority — strongest signal first, so a partner that
        was matched or created in a previous sync isn't re-matched
        by weaker signals that could point elsewhere:

          1. **Clover customer_id** — stamped on res.partner as
             ``x_clover_customer_id`` after the first successful
             resolve. Absolute: only one partner per Clover customer,
             ever.
          2. **Phone** (last 10 digits) — matched against
             res.partner.phone / mobile.
          3. **Email** (case-insensitive) — matched against
             res.partner.email.

        The Clover customer id, when present, is ALWAYS stamped on
        the resolved partner (matched or created) so the next sync
        skips fuzzy matching entirely.

        Dedup rules to prevent junk contacts:
        * Refuse to auto-create if the Clover record has neither
          phone nor email — a name alone is not a strong enough
          identity to spawn a contact.
        * The ``dedup_cache`` dict (keyed by clover customer_id, or
          normalized phone, or lowercased email) is honoured first
          so two orders from the same Clover customer within one
          sync run never race to create two partners.

        Returns an empty recordset when no match is found and
        auto-create is off or refused for sparsity reasons — the
        caller keeps the raw name/email/phone strings on
        clover.sale so the info isn't lost.

        :param dry_run: When True, does NOT create partners or stamp
            ``x_clover_customer_id`` — only reports what WOULD happen.
            Used by the preview wizard.
        """
        self.ensure_one()
        Partner = self.env["res.partner"].sudo()
        cd = customer_dict or {}
        cache = dedup_cache if dedup_cache is not None else {}

        clover_cust_id = (cd.get("id") or "").strip()
        phones = [(p.get("phoneNumber") or "")
                  for p in (cd.get("phoneNumbers", {})
                            .get("elements") or [])]
        emails = [(e.get("emailAddress") or "").strip().lower()
                  for e in (cd.get("emailAddresses", {})
                            .get("elements") or [])
                  if e.get("emailAddress")]

        # ------- Cache lookup (same-run duplicate protection) -------
        def _cache_get():
            if clover_cust_id and cache.get(("cid", clover_cust_id)):
                return Partner.browse(cache[("cid", clover_cust_id)])
            for raw in phones:
                norm = self._clover_normalize_phone(raw)
                if norm and cache.get(("phone", norm)):
                    return Partner.browse(cache[("phone", norm)])
            for email in emails:
                if cache.get(("email", email)):
                    return Partner.browse(cache[("email", email)])
            return Partner.browse()

        cached = _cache_get()
        if cached:
            return cached

        # ------- 1. Clover customer_id (stamped from prior sync) ----
        found = Partner.browse()
        if clover_cust_id:
            found = Partner.search(
                [("x_clover_customer_id", "=", clover_cust_id)],
                limit=1,
            )

        # ------- 2. Phone match — normalize both sides --------------
        if not found:
            for raw in phones:
                norm = self._clover_normalize_phone(raw)
                if not norm:
                    continue
                candidates = Partner.search([
                    "|",
                    ("phone", "ilike", norm),
                    ("mobile", "ilike", norm),
                ], limit=10)
                for c in candidates:
                    if (self._clover_normalize_phone(c.phone) == norm
                            or self._clover_normalize_phone(c.mobile)
                            == norm):
                        found = c
                        break
                if found:
                    break

        # ------- 3. Email match -------------------------------------
        if not found:
            for email in emails:
                match = Partner.search(
                    [("email", "=ilike", email)], limit=1,
                )
                if match:
                    found = match
                    break

        # ------- 4. Auto-create (opt-in AND enough identity) --------
        if not found and self.clover_auto_create_partners:
            if not phones and not emails:
                # Name-only Clover customers are almost always
                # walk-in noise — refuse to spawn a contact.
                _logger.info(
                    "Clover: skipping auto-create for sparse "
                    "customer (no phone, no email). clover_cust_id=%s",
                    clover_cust_id or "-",
                )
            elif dry_run:
                # Preview mode — return empty; caller reports the
                # WOULD-CREATE decision without actually creating.
                return Partner.browse()
            else:
                first = (cd.get("firstName") or "").strip()
                last = (cd.get("lastName") or "").strip()
                name = (f"{first} {last}").strip() or "Clover Customer"
                create_vals = {
                    "name": name,
                    "email": emails[0] if emails else False,
                    "phone": phones[0] if phones else False,
                    "comment": "Auto-created from Clover POS sync.",
                    "x_clover_customer_id": clover_cust_id or False,
                }
                found = Partner.create(create_vals)

        # ------- Stamp Clover customer_id + populate cache ----------
        # In dry_run we skip the stamp (it's a write) but still cache
        # by IDs already in the DB so same-batch dedup works.
        if found:
            if not dry_run:
                if clover_cust_id and not found.x_clover_customer_id:
                    found.x_clover_customer_id = clover_cust_id
            if clover_cust_id:
                cache[("cid", clover_cust_id)] = found.id
            for raw in phones:
                norm = self._clover_normalize_phone(raw)
                if norm:
                    cache[("phone", norm)] = found.id
            for email in emails:
                cache[("email", email)] = found.id

        return found

    # ------------------------------------------------------------------
    # Sync orchestration
    # ------------------------------------------------------------------

    def _clover_platform_paginated(self, path_no_paging, params=None,
                                    page_size=1000):
        """Iterate every element from a paginated Clover Platform endpoint.

        Yields dicts. Handles the {"elements": [...], "href": ...}
        wrapper and auto-advances offset until fewer than page_size
        results come back.
        """
        self.ensure_one()
        params = dict(params or {})
        offset = 0
        while True:
            params["limit"] = page_size
            params["offset"] = offset
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            sep = "&" if "?" in path_no_paging else "?"
            path = f"{path_no_paging}{sep}{qs}"
            data = self._clover_platform_request("GET", path)
            elements = (data or {}).get("elements", [])
            if not elements:
                return
            for el in elements:
                yield el
            if len(elements) < page_size:
                return
            offset += page_size

    def action_clover_open_preview(self):
        """Open the Preview / Dry Run wizard for THIS provider.

        Called from the header button on the provider form and from
        the "Preview Clover Data" menu item.
        """
        self.ensure_one()
        wiz = self.env["clover.sync.preview.wizard"].create({
            "provider_id": self.id,
            "sample_size": 25,
        })
        wiz.action_refresh_preview()
        return {
            "type": "ir.actions.act_window",
            "name": _("Clover Sync — Preview"),
            "res_model": "clover.sync.preview.wizard",
            "res_id": wiz.id,
            "view_mode": "form",
            "target": "new",
        }

    @api.model
    def action_clover_open_preview_menu(self):
        """Entry point for the menu (no provider recordset in self).

        Picks the sole enabled Clover provider and opens the wizard
        for it. If none, shows a warning notification.
        """
        provider = self.search([
            ("code", "=", "clover"),
            ("clover_sync_sales", "=", True),
        ], limit=1)
        if not provider:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Clover Preview"),
                    "message": _("Enable 'Sync Clover Sales' on the "
                                 "Clover provider first."),
                    "type": "warning",
                },
            }
        return provider.action_clover_open_preview()

    def action_clover_sync_now(self):
        """Menu-triggered on-demand sync.

        Runs the same pipeline as the nightly cron for every provider
        where clover_sync_sales is enabled. Shows a summary
        notification when done.
        """
        providers = self or self.env["payment.provider"].search([
            ("code", "=", "clover"),
            ("clover_sync_sales", "=", True),
        ])
        if not providers:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Clover Sync"),
                    "message": _("No Clover providers have 'Sync "
                                 "Clover Sales' enabled."),
                    "type": "warning",
                },
            }
        stats = providers._clover_run_full_sync()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Clover Sync Complete"),
                "message": _(
                    "Employees: %(emp)s linked, %(emp_new)s new "
                    "mirror rows, %(hr)s hr.employees auto-created. "
                    "Sales: %(sales)s orders, %(tips)s tip entries.",
                    emp=stats.get("employees_linked", 0),
                    emp_new=stats.get("employees_new", 0),
                    hr=stats.get("hr_created", 0),
                    sales=stats.get("sales", 0),
                    tips=stats.get("tips", 0),
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def _clover_run_full_sync(self):
        """Run every sync step for the current recordset, in order.

        Called by the cron and by action_clover_sync_now.
        Wraps every step in the silent context and swallows per-step
        exceptions with logging so one broken provider doesn't stop
        the others.
        """
        totals = {"employees_linked": 0, "employees_new": 0,
                  "hr_created": 0, "sales": 0, "tips": 0}
        for provider in self:
            if provider.code != "clover" or not provider.clover_sync_sales:
                continue
            silent = provider._clover_silent_context()
            provider_s = provider.with_context(**silent)
            try:
                emp_stats = provider_s._clover_sync_employees()
                totals["employees_linked"] += emp_stats.get("linked", 0)
                totals["employees_new"] += emp_stats.get("new", 0)
                totals["hr_created"] += emp_stats.get("hr_created", 0)
            except Exception as e:  # noqa: BLE001
                _logger.exception(
                    "Clover: employee sync failed for provider %s: %s",
                    provider.id, e)
            try:
                sales_stats = provider_s._clover_sync_sales()
                totals["sales"] += sales_stats.get("sales", 0)
                totals["tips"] += sales_stats.get("tips", 0)
            except Exception as e:  # noqa: BLE001
                _logger.exception(
                    "Clover: sales sync failed for provider %s: %s",
                    provider.id, e)
            # Item cost sync — cheap; only if enabled.
            if provider.clover_sync_item_costs:
                try:
                    provider_s._clover_sync_item_costs()
                except Exception as e:  # noqa: BLE001
                    _logger.exception(
                        "Clover: item cost sync failed for provider %s: %s",
                        provider.id, e)
        return totals

    @api.model
    def _clover_cron_run_nightly_sync(self):
        """Entry point for the ir.cron scheduled task."""
        providers = self.env["payment.provider"].search([
            ("code", "=", "clover"),
            ("clover_sync_sales", "=", True),
        ])
        if not providers:
            _logger.info("Clover nightly sync: no eligible providers.")
            return
        _logger.info(
            "Clover nightly sync: starting for %s provider(s).",
            len(providers))
        providers._clover_run_full_sync()
        _logger.info("Clover nightly sync: complete.")

    # ------------------------------------------------------------------
    # Employee sync
    # ------------------------------------------------------------------

    def _clover_sync_employees(self):
        """Pull employees from Clover Platform and mirror as clover.employee.

        Auto-matches to hr.employee by (in order) email, phone, then
        exact name. Unmatched Clover employees (e.g. the 'Float'
        profile Clover creates by default) are stored as clover.employee
        rows with is_float=True and no employee_id — a human can link
        them later from the list view.
        """
        self.ensure_one()
        if not self.clover_merchant_id:
            return {"linked": 0, "new": 0}
        HrEmp = self.env["hr.employee"].sudo()
        CloverEmp = self.env["clover.employee"].sudo()

        stats = {"linked": 0, "new": 0, "hr_created": 0}
        path = f"/v3/merchants/{self.clover_merchant_id}/employees"
        for el in self._clover_platform_paginated(path):
            clover_id = el.get("id")
            if not clover_id:
                continue
            name = el.get("name") or el.get("nickname") or "Unknown"
            nickname = el.get("nickname") or ""
            email = (el.get("email") or "").strip().lower()
            role = el.get("role") or ""
            # Clover creates a default "Float" profile that isn't a
            # real person. Flag it so it's excluded from tip reports.
            is_float = (
                nickname.lower() == "float"
                or name.lower() == "float"
                or role.upper() == "FLOAT"
            )

            existing = CloverEmp.with_context(active_test=False).search([
                ("provider_id", "=", self.id),
                ("clover_employee_id", "=", clover_id),
            ], limit=1)

            employee_id = existing.employee_id.id if existing else False
            if not employee_id and not is_float:
                # Try to match an existing hr.employee — email is
                # the strongest signal, exact name is a fallback.
                match = HrEmp
                if email:
                    match = HrEmp.search([
                        ("work_email", "=ilike", email),
                    ], limit=1)
                if not match:
                    match = HrEmp.search([
                        ("name", "=ilike", name),
                    ], limit=1)
                if match:
                    employee_id = match.id
                    stats["linked"] += 1
                else:
                    # No hr.employee for this Clover cashier —
                    # auto-create one silently. Every Clover cashier
                    # must exist in Odoo so their sales/tips can be
                    # attributed. Marked x_clover_auto_created=True
                    # so admins can review and finish setup
                    # (department, work phone, manager, etc.).
                    new_emp = HrEmp.create({
                        "name": name,
                        "work_email": email or False,
                        "x_clover_auto_created": True,
                    })
                    employee_id = new_emp.id
                    stats["hr_created"] += 1
                    _logger.info(
                        "Clover: auto-created hr.employee %s "
                        "(%s) for Clover cashier %s",
                        new_emp.id, name, clover_id,
                    )

            vals = {
                "name": name,
                "nickname": nickname,
                "email": email or False,
                "role": role,
                "is_float": is_float,
                "employee_id": employee_id,
                "last_synced": fields.Datetime.now(),
            }
            if existing:
                existing.write(vals)
                # Auto-set exclude_from_reports for Float on refresh
                # WITHOUT clobbering an admin's manual OFF choice on
                # any other account: only touch it when it doesn't
                # already match the auto-value.
                if is_float and not existing.exclude_from_reports:
                    existing.exclude_from_reports = True
            else:
                vals.update({
                    "provider_id": self.id,
                    "clover_employee_id": clover_id,
                    # First-time create: exclude Float automatically.
                    # Everything else defaults to False; admin can
                    # flip individual contractor / non-employee
                    # accounts in the Clover Employees list.
                    "exclude_from_reports": is_float,
                })
                CloverEmp.create(vals)
                stats["new"] += 1

        self.clover_last_employee_sync_at = fields.Datetime.now()
        return stats

    # ------------------------------------------------------------------
    # Sales sync
    # ------------------------------------------------------------------

    def _clover_sync_sales(self, since_dt=None):
        """Pull orders + payments modified since since_dt (or last sync).

        Creates/updates clover.sale + clover.sale.line rows, and
        clover.tip.entry rows for any payment with tipAmount > 0.
        Optionally also creates sale.order records when
        clover_auto_create_sale_orders is on (with state='sale' set
        directly — no confirmation email).
        """
        self.ensure_one()
        if not self.clover_merchant_id:
            return {"sales": 0, "tips": 0}

        CloverSale = self.env["clover.sale"].sudo()
        CloverTip = self.env["clover.tip.entry"].sudo()
        CloverEmp = self.env["clover.employee"].sudo()

        # Compute since — default: last successful sync minus 1h
        # overlap (idempotent upserts protect us from double-counting
        # across the overlap window).
        from datetime import timedelta
        since = since_dt or self.clover_last_sales_sync_at
        if since:
            since = since - timedelta(hours=1)
        params = {
            "expand": ("lineItems,payments,payments.tender,"
                       "payments.employee,customers,employee"),
        }
        if since:
            since_ms = int(since.timestamp() * 1000)
            params["filter"] = f"modifiedTime>={since_ms}"

        path = f"/v3/merchants/{self.clover_merchant_id}/orders"
        stats = {"sales": 0, "tips": 0}
        run_start = fields.Datetime.now()
        # Per-run cache so the same Clover customer (identified by
        # customer_id, phone, or email) never produces more than one
        # res.partner across all the orders in this sync pass.
        partner_cache = {}

        for order in self._clover_platform_paginated(
                path, params=params, page_size=200):
            clover_order_id = order.get("id")
            if not clover_order_id:
                continue

            # Map Clover cashier → clover.employee → hr.employee.
            emp = (order.get("employee") or {})
            emp_clover_id = emp.get("id")
            emp_link = CloverEmp.search([
                ("provider_id", "=", self.id),
                ("clover_employee_id", "=", emp_clover_id),
            ], limit=1) if emp_clover_id else CloverEmp.browse()

            total_cents = order.get("total", 0) or 0
            paid_cents = order.get("paymentState") == "PAID"
            state = (order.get("state") or "").upper()
            ts_ms = order.get("createdTime") or order.get("modifiedTime") or 0
            # Clover timestamps are UTC epoch milliseconds. Odoo stores
            # datetimes as naive UTC, so a bare utcfromtimestamp is
            # what we want.
            import datetime as _dt
            ts_dt = (_dt.datetime.utcfromtimestamp(ts_ms / 1000.0)
                     if ts_ms else fields.Datetime.now())

            cust = ((order.get("customers") or {}).get("elements") or [{}])[0]
            cust_first = (cust.get("firstName") or "").strip()
            cust_last = (cust.get("lastName") or "").strip()
            cust_name = (f"{cust_first} {cust_last}").strip() or ""
            cust_emails = cust.get("emailAddresses", {}).get("elements", [])
            cust_email = (cust_emails[0].get("emailAddress")
                          if cust_emails else "")
            cust_phones = cust.get("phoneNumbers", {}).get("elements", [])
            cust_phone = (cust_phones[0].get("phoneNumber")
                          if cust_phones else "")

            # Try to link to an existing res.partner. Priority:
            #   Clover customer_id (stamped from prior sync) → phone →
            #   email. The dedup cache passed through the loop stops
            #   two orders from the same Clover customer within THIS
            #   run from creating two partners. Auto-create is
            #   refused when the Clover record has no phone/email
            #   (name alone is too weak).
            partner = self._clover_resolve_partner(
                cust, dedup_cache=partner_cache,
            )

            sale_vals = {
                "provider_id": self.id,
                "clover_order_id": clover_order_id,
                "date": ts_dt,
                "employee_id": emp_link.employee_id.id or False,
                "clover_employee_id": emp_link.id or False,
                "partner_id": partner.id or False,
                "customer_name": cust_name,
                "customer_email": cust_email,
                "customer_phone": cust_phone,
                "total_amount": total_cents / 100.0,
                "currency_code": (order.get("currency")
                                  or "USD").upper(),
                "state": state or "open",
                "is_paid": bool(paid_cents),
                "last_synced": fields.Datetime.now(),
            }
            sale = CloverSale.with_context(active_test=False).search([
                ("provider_id", "=", self.id),
                ("clover_order_id", "=", clover_order_id),
            ], limit=1)
            if sale:
                sale.write(sale_vals)
                sale.line_ids.unlink()
            else:
                sale = CloverSale.create(sale_vals)
            stats["sales"] += 1

            # Line items
            line_items = ((order.get("lineItems") or {}).get("elements")
                          or [])
            for li in line_items:
                item = li.get("item") or {}
                line_vals = {
                    "sale_id": sale.id,
                    "clover_line_id": li.get("id") or "",
                    "clover_item_id": item.get("id") or "",
                    "description": li.get("name") or "",
                    "unit_qty": li.get("unitQty") or 0,
                    "amount": (li.get("price", 0) or 0) / 100.0,
                    "note": (li.get("note") or "")[:500],
                }
                self.env["clover.sale.line"].sudo().create(line_vals)

            # Tip entries — one row per payment with a tip.
            payments = ((order.get("payments") or {}).get("elements") or [])
            for pay in payments:
                pay_id = pay.get("id")
                tip_cents = pay.get("tipAmount", 0) or 0
                if not pay_id:
                    continue
                pay_emp = (pay.get("employee") or {})
                pay_emp_clover_id = pay_emp.get("id") or emp_clover_id
                pay_emp_link = CloverEmp.search([
                    ("provider_id", "=", self.id),
                    ("clover_employee_id", "=", pay_emp_clover_id),
                ], limit=1) if pay_emp_clover_id else CloverEmp.browse()

                is_refund = (pay.get("result") == "REFUNDED"
                             or (pay.get("refunds") or {})
                             .get("elements"))

                # is_event_gratuity is driven off the Clover
                # employee's exclude_from_reports flag (auto-True for
                # Float, admin-editable for contractor / one-off /
                # shared accounts). Falling back to False when no
                # clover.employee link exists means unassigned tips
                # DO show up in personal reports as unassigned rows
                # — a signal to link the account, not a silent drop.
                pay_gratuity = (pay_emp_link.exclude_from_reports
                                if pay_emp_link else False)

                # is_unclaimed = the linked hr.employee's department
                # is flagged x_clover_tips_eligible=False (Volunteers,
                # salaried managers, shared-role departments).
                # Recorded against the employee for audit, but stays
                # OUT of their personal tip total AND separate from
                # pooled event gratuity — managers allocate these
                # separately.
                is_unclaimed = False
                if pay_emp_link and pay_emp_link.employee_id:
                    dept = pay_emp_link.employee_id.department_id
                    if dept and not dept.x_clover_tips_eligible:
                        is_unclaimed = True

                tip_vals = {
                    "provider_id": self.id,
                    "clover_payment_id": pay_id,
                    "clover_sale_id": sale.id,
                    "date": ts_dt,
                    "amount": tip_cents / 100.0,
                    "employee_id": pay_emp_link.employee_id.id or False,
                    "clover_employee_id": pay_emp_link.id or False,
                    "is_event_gratuity": pay_gratuity,
                    "is_unclaimed": is_unclaimed,
                    "is_refunded": bool(is_refund),
                    "last_synced": fields.Datetime.now(),
                }
                if tip_cents <= 0 and not is_refund:
                    continue
                existing_tip = CloverTip.search([
                    ("provider_id", "=", self.id),
                    ("clover_payment_id", "=", pay_id),
                ], limit=1)
                if existing_tip:
                    existing_tip.write(tip_vals)
                else:
                    CloverTip.create(tip_vals)
                    if tip_cents > 0:
                        stats["tips"] += 1

            # Optional: also create a sale.order (silent).
            if self.clover_auto_create_sale_orders:
                sale._clover_ensure_sale_order()

        self.clover_last_sales_sync_at = run_start
        return stats

    # ------------------------------------------------------------------
    # Dry-run preview (no writes)
    # ------------------------------------------------------------------

    def _clover_preview_sync(self, sample_size=25):
        """Fetch a sample of Clover data and simulate the sync decisions.

        Returns (employee_decisions, sale_decisions) — two lists of
        dicts suitable for creating clover.sync.preview.* rows.
        Commits NOTHING; only reads from Clover + the Odoo DB.

        Used by the Preview wizard to show admins exactly what a
        real sync would do to their data, before they commit.
        """
        self.ensure_one()
        if not self.clover_merchant_id:
            return [], []

        HrEmp = self.env["hr.employee"].sudo()
        CloverEmp = self.env["clover.employee"].sudo()
        CloverSale = self.env["clover.sale"].sudo()

        # -------- Employees --------------------------------------------
        emp_decisions = []
        emp_path = f"/v3/merchants/{self.clover_merchant_id}/employees"
        for el in self._clover_platform_paginated(emp_path):
            clover_id = el.get("id")
            if not clover_id:
                continue
            name = el.get("name") or el.get("nickname") or "Unknown"
            nickname = el.get("nickname") or ""
            email = (el.get("email") or "").strip().lower()
            role = el.get("role") or ""
            is_float = (
                nickname.lower() == "float"
                or name.lower() == "float"
                or role.upper() == "FLOAT"
            )

            existing = CloverEmp.with_context(active_test=False).search([
                ("provider_id", "=", self.id),
                ("clover_employee_id", "=", clover_id),
            ], limit=1)

            decision = {
                "clover_employee_id": clover_id,
                "name": name,
                "nickname": nickname,
                "email": email or False,
                "role": role,
            }

            if is_float:
                decision.update({
                    "action": "float",
                    "matched_employee_id": False,
                    "match_reason": "Detected as Clover Float profile",
                })
                emp_decisions.append(decision)
                continue

            if existing and existing.employee_id:
                decision.update({
                    "action": "unchanged",
                    "matched_employee_id": existing.employee_id.id,
                    "match_reason": (
                        "Already linked in a previous sync"
                    ),
                })
                emp_decisions.append(decision)
                continue

            # Try to match — same logic as _clover_sync_employees.
            match = HrEmp.browse()
            reason = ""
            if email:
                match = HrEmp.search([
                    ("work_email", "=ilike", email),
                ], limit=1)
                if match:
                    reason = f"Matched by work_email = {email}"
            if not match:
                match = HrEmp.search([
                    ("name", "=ilike", name),
                ], limit=1)
                if match:
                    reason = f"Matched by exact name = {name}"

            if match:
                decision.update({
                    "action": "link",
                    "matched_employee_id": match.id,
                    "match_reason": reason,
                })
            else:
                decision.update({
                    "action": "create",
                    "matched_employee_id": False,
                    "match_reason": (
                        "No hr.employee matched by email or name — "
                        "would auto-create silently"
                    ),
                })
            emp_decisions.append(decision)

        # -------- Sales (last N orders) -------------------------------
        sale_decisions = []
        sale_params = {
            "expand": ("lineItems,payments,payments.tender,"
                       "payments.employee,customers,employee"),
            "limit": sample_size,
        }
        # We DON'T use a modifiedTime filter here — preview shows the
        # freshest N orders regardless of what the last sync saw.
        sale_path = (
            f"/v3/merchants/{self.clover_merchant_id}/orders"
            f"?limit={int(sample_size)}"
        )
        try:
            data = self._clover_platform_request("GET", sale_path)
        except Exception as e:  # noqa: BLE001
            _logger.warning("Clover preview: order fetch failed: %s", e)
            return emp_decisions, sale_decisions
        orders = (data or {}).get("elements", []) or []

        # Partner-resolution cache — same idea as the real sync's
        # partner_cache, so we correctly show "match" for a customer
        # who appears in multiple orders in this preview batch.
        partner_cache = {}

        import datetime as _dt
        for order in orders:
            clover_order_id = order.get("id")
            if not clover_order_id:
                continue
            ts_ms = (order.get("createdTime")
                     or order.get("modifiedTime") or 0)
            ts_dt = (_dt.datetime.utcfromtimestamp(ts_ms / 1000.0)
                     if ts_ms else fields.Datetime.now())

            emp = (order.get("employee") or {})
            cashier_name = emp.get("name") or emp.get("nickname") or ""

            cust = ((order.get("customers") or {})
                    .get("elements") or [{}])[0]
            cust_first = (cust.get("firstName") or "").strip()
            cust_last = (cust.get("lastName") or "").strip()
            customer_name = (f"{cust_first} {cust_last}").strip()
            emails = [(e.get("emailAddress") or "").strip().lower()
                      for e in (cust.get("emailAddresses", {})
                                .get("elements") or [])
                      if e.get("emailAddress")]
            phones = [(p.get("phoneNumber") or "")
                      for p in (cust.get("phoneNumbers", {})
                                .get("elements") or [])]
            customer_email = emails[0] if emails else ""
            customer_phone = phones[0] if phones else ""

            total_cents = order.get("total", 0) or 0
            line_items = ((order.get("lineItems") or {})
                          .get("elements") or [])
            payments = ((order.get("payments") or {})
                        .get("elements") or [])
            tip_cents = sum((p.get("tipAmount", 0) or 0)
                            for p in payments)

            # Would this be a create or an update?
            existing_sale = CloverSale.search([
                ("provider_id", "=", self.id),
                ("clover_order_id", "=", clover_order_id),
            ], limit=1)
            sale_action = "update" if existing_sale else "create"

            # Partner-resolution preview — dry_run=True means the
            # resolver reports what would happen but creates nothing.
            cid = (cust.get("id") or "").strip()
            partner = self._clover_resolve_partner(
                cust, dedup_cache=partner_cache, dry_run=True,
            )

            partner_action = "skip"
            partner_reason = ""
            if partner:
                # Match found in DB — figure out which signal won.
                if partner.x_clover_customer_id == cid and cid:
                    partner_reason = (
                        f"Matched by Clover customer_id — "
                        f"{partner.name}"
                    )
                elif customer_phone and (
                        self._clover_normalize_phone(partner.phone)
                        == self._clover_normalize_phone(customer_phone)):
                    partner_reason = f"Matched by phone — {partner.name}"
                elif customer_phone and (
                        self._clover_normalize_phone(partner.mobile)
                        == self._clover_normalize_phone(customer_phone)):
                    partner_reason = f"Matched by mobile — {partner.name}"
                elif (customer_email and partner.email
                      and partner.email.lower() == customer_email):
                    partner_reason = f"Matched by email — {partner.name}"
                else:
                    partner_reason = f"Matched — {partner.name}"
                partner_action = "match"
            else:
                # No DB match. Decide would-happen based on toggle
                # and sparsity — same rules as the real resolver.
                if not customer_phone and not customer_email:
                    partner_reason = (
                        "No phone/email on Clover customer — "
                        "would skip (too sparse)"
                    )
                    partner_action = "skip"
                elif not self.clover_auto_create_partners:
                    partner_reason = (
                        "No existing match — Auto-Create Contacts "
                        "toggle is OFF, so would skip"
                    )
                    partner_action = "skip"
                else:
                    first = (cust.get("firstName") or "").strip()
                    last = (cust.get("lastName") or "").strip()
                    would_name = (f"{first} {last}").strip() or (
                        "Clover Customer")
                    partner_reason = (
                        f"No existing match — would auto-create "
                        f"silently as \"{would_name}\""
                    )
                    partner_action = "create"

            sale_decisions.append({
                "clover_order_id": clover_order_id,
                "date": ts_dt,
                "cashier_name": cashier_name,
                "customer_name": customer_name,
                "customer_phone": customer_phone,
                "customer_email": customer_email,
                "total_amount": total_cents / 100.0,
                "tip_amount": tip_cents / 100.0,
                "line_count": len(line_items),
                "sale_action": sale_action,
                "partner_action": partner_action,
                "matched_partner_id": partner.id if partner else False,
                "partner_reason": partner_reason,
            })

        return emp_decisions, sale_decisions

    # ------------------------------------------------------------------
    # Item cost sync
    # ------------------------------------------------------------------

    def _clover_sync_item_costs(self):
        """Refresh Clover item costs and push to product.product.standard_price.

        Called from the full-sync pipeline when clover_sync_item_costs
        is on. Reads item.cost (cents) from Clover Platform API and
        writes it (dollars) to the linked Odoo product's Cost field.
        """
        self.ensure_one()
        if not self.clover_merchant_id:
            return
        path = f"/v3/merchants/{self.clover_merchant_id}/items"
        CloverItem = self.env["clover.item"].sudo()
        for el in self._clover_platform_paginated(path):
            clover_id = el.get("id")
            if not clover_id:
                continue
            cost_cents = el.get("cost", 0) or 0
            cost_dollars = cost_cents / 100.0
            item = CloverItem.with_context(active_test=False).search([
                ("provider_id", "=", self.id),
                ("clover_item_id", "=", clover_id),
            ], limit=1)
            if not item:
                continue
            if item.cost != cost_dollars:
                item.cost = cost_dollars
            if item.product_id and item.product_id.standard_price != cost_dollars:
                item.product_id.sudo().standard_price = cost_dollars

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
                cost_cents = item.get("cost", 0) or 0
                cost_dollars = cost_cents / 100.0

                vals = {
                    "name": item.get("name", "Unknown Item"),
                    "price": price_dollars,
                    "cost": cost_dollars,
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
