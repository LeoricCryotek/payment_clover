/** @odoo-module **/

/**
 * Clover Payment Terminal — staff-facing payment screen.
 *
 * Registered as a client action (tag: payment_clover.terminal).
 * Staff can select a partner, enter an amount, swipe/type a card
 * via Clover's iframe, and process the payment immediately.
 */

import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";


class CloverPaymentTerminal extends Component {
    static template = "payment_clover.PaymentTerminal";
    static props = ["*"];

    setup() {
        this.notification = useService("notification");
        this.orm = useService("orm");

        this.state = useState({
            step: "form",         // form | processing | result
            providers: [],
            selectedProviderId: null,
            partners: [],
            selectedPartnerId: null,
            partnerSearch: "",
            amount: "",
            description: "",
            currency: null,
            currencies: [],
            selectedCurrencyId: null,
            // Item picker
            cloverItems: [],
            selectedItemId: null,
            itemsLoading: false,
            // Guest checkout
            isGuest: false,
            // Card reader status
            cardReady: false,
            cardError: "",
            // Result
            resultStatus: "",
            resultMessage: "",
            resultReference: "",
        });

        this._cloverInstance = null;
        this._cloverElements = {};

        onMounted(() => this._loadData());
        onWillUnmount(() => this._destroyClover());
    }

    async _loadData() {
        // Load Clover providers
        const providers = await this.orm.searchRead(
            "payment.provider",
            [["code", "=", "clover"], ["state", "in", ["enabled", "test"]]],
            ["id", "name", "clover_pakms_key", "clover_merchant_id", "state"],
        );
        this.state.providers = providers;
        if (providers.length === 1) {
            this.state.selectedProviderId = providers[0].id;
        }

        // Default to USD
        const currencies = await this.orm.searchRead(
            "res.currency",
            [["name", "=", "USD"], ["active", "=", true]],
            ["id", "name", "symbol"],
            {limit: 1},
        );
        if (currencies.length > 0) {
            this.state.selectedCurrencyId = currencies[0].id;
        }

        // Auto-initialize card reader and load items if we have a provider
        if (this.state.selectedProviderId) {
            // Small delay to let the DOM render the card field containers
            await new Promise(r => setTimeout(r, 200));
            await Promise.all([
                this.initCloverIframe(),
                this._loadItems(),
            ]);
        }
    }

    async onSearchPartner() {
        const query = this.state.partnerSearch.trim();
        if (query.length < 2) {
            this.state.partners = [];
            return;
        }
        const partners = await this.orm.searchRead(
            "res.partner",
            ["|", "|",
                ["name", "ilike", query],
                ["email", "ilike", query],
                ["phone", "ilike", query],
            ],
            ["id", "name", "email"],
            {limit: 10},
        );
        this.state.partners = partners;
    }

    selectPartner(partnerId) {
        this.state.selectedPartnerId = partnerId;
        const partner = this.state.partners.find(p => p.id === partnerId);
        if (partner) {
            this.state.partnerSearch = partner.name;
        }
        this.state.partners = [];
        this.state.isGuest = false;
    }

    toggleGuest() {
        this.state.isGuest = !this.state.isGuest;
        if (this.state.isGuest) {
            this.state.selectedPartnerId = null;
            this.state.partnerSearch = "";
            this.state.partners = [];
        }
    }

    async _loadItems() {
        if (!this.state.selectedProviderId) {
            return;
        }
        this.state.itemsLoading = true;
        try {
            const result = await rpc("/payment/clover/terminal/items", {
                provider_id: this.state.selectedProviderId,
            });
            this.state.cloverItems = result.items || [];
        } catch (e) {
            console.warn("[Clover Terminal] Failed to load items:", e);
            this.state.cloverItems = [];
        }
        this.state.itemsLoading = false;
    }

    selectItem(ev) {
        const itemId = parseInt(ev.target.value);
        this.state.selectedItemId = itemId || null;
        if (!itemId) {
            return;
        }
        const item = this.state.cloverItems.find(i => i.id === itemId);
        if (item) {
            if (item.price && item.price_type === "FIXED") {
                this.state.amount = item.price.toFixed(2);
            }
            this.state.description = item.name;
        }
    }

    async _getOrCreateGuestPartner() {
        try {
            const result = await rpc("/payment/clover/terminal/guest_partner", {});
            return result.partner_id || null;
        } catch (e) {
            console.error("[Clover Terminal] Guest partner error:", e);
            return null;
        }
    }

    async initCloverIframe() {
        // Reset state for re-init
        this.state.cardReady = false;
        this.state.cardError = "";
        this._destroyClover();

        const provider = this.state.providers.find(
            p => p.id === this.state.selectedProviderId
        );
        if (!provider || !provider.clover_pakms_key) {
            this.state.cardError = "Provider missing PAKMS key.";
            this.notification.add(_t("Provider missing PAKMS key."), {type: "danger"});
            return;
        }

        // Determine SDK URL based on provider state
        const sdkUrl = provider.state === "test"
            ? "https://checkout.sandbox.dev.clover.com/sdk.js"
            : "https://checkout.clover.com/sdk.js";

        // Load SDK
        if (!window.Clover) {
            try {
                await this._loadScript(sdkUrl);
            } catch (e) {
                this.state.cardError = "Failed to load Clover SDK.";
                this.notification.add(_t("Failed to load Clover SDK."), {type: "danger"});
                return;
            }
        }
        if (!window.Clover) {
            this.state.cardError = "Clover SDK not available.";
            this.notification.add(_t("Failed to load Clover SDK."), {type: "danger"});
            return;
        }

        try {
            const pakmsKey = provider.clover_pakms_key;
            const merchantId = provider.clover_merchant_id;
            const envLabel = provider.state === "test" ? "SANDBOX" : "PRODUCTION";
            console.log(
                `[Clover Terminal] Initializing in ${envLabel} mode. ` +
                `PAKMS key starts with: ${pakmsKey.substring(0, 8)}… ` +
                `Merchant ID: ${merchantId || "(not set)"}`
            );

            // merchantId is required for reCAPTCHA and full tokenization support
            const cloverOpts = {};
            if (merchantId) {
                cloverOpts.merchantId = merchantId;
            }
            this._cloverInstance = new window.Clover(pakmsKey, cloverOpts);
            const elements = this._cloverInstance.elements();

            this._cloverElements.cardNumber = elements.create("CARD_NUMBER");
            this._cloverElements.cardDate = elements.create("CARD_DATE");
            this._cloverElements.cardCvv = elements.create("CARD_CVV");
            this._cloverElements.cardPostal = elements.create("CARD_POSTAL_CODE");

            // Wait a tick for DOM to render
            await new Promise(r => setTimeout(r, 100));

            this._cloverElements.cardNumber.mount("#terminal-card-number");
            this._cloverElements.cardDate.mount("#terminal-card-date");
            this._cloverElements.cardCvv.mount("#terminal-card-cvv");
            this._cloverElements.cardPostal.mount("#terminal-card-postal");

            console.log("[Clover Terminal] Card elements mounted successfully.");
            this.state.cardReady = true;
        } catch (e) {
            console.error("[Clover Terminal] Init error:", e);
            this.state.cardError = "Could not initialize card reader: " + (e.message || "");
            this.notification.add(
                _t("Could not initialize card reader: ") + (e.message || ""),
                {type: "danger"},
            );
        }
    }

    /**
     * Wrap createToken with a timeout so the UI never hangs forever.
     */
    _createTokenWithTimeout(timeoutMs = 30000) {
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                reject(new Error(
                    "Card tokenization timed out after " +
                    (timeoutMs / 1000) + " seconds. " +
                    "Please re-initialize the card reader and try again."
                ));
            }, timeoutMs);

            this._cloverInstance.createToken()
                .then(result => {
                    clearTimeout(timer);
                    resolve(result);
                })
                .catch(err => {
                    clearTimeout(timer);
                    reject(err);
                });
        });
    }

    async processPayment() {
        // Validate
        if (!this.state.selectedProviderId) {
            this.notification.add(_t("Please select a Clover provider."), {type: "warning"});
            return;
        }
        const amount = parseFloat(this.state.amount);
        if (!amount || amount <= 0) {
            this.notification.add(_t("Please enter a valid amount."), {type: "warning"});
            return;
        }
        // Resolve partner: guest walk-in or selected customer
        let partnerId = this.state.selectedPartnerId;
        if (this.state.isGuest) {
            partnerId = await this._getOrCreateGuestPartner();
            if (!partnerId) {
                this.notification.add(
                    _t("Could not create guest partner record."),
                    {type: "danger"},
                );
                return;
            }
        }
        if (!partnerId) {
            this.notification.add(_t("Please select a customer or use Guest checkout."), {type: "warning"});
            return;
        }
        if (!this._cloverInstance || !this.state.cardReady) {
            this.notification.add(
                _t("Card reader not ready. Click 'Re-initialize Card Reader' and try again."),
                {type: "warning"},
            );
            return;
        }

        this.state.step = "processing";

        // Tokenize with timeout
        let tokenResult;
        try {
            console.log("[Clover Terminal] Calling createToken()...");
            tokenResult = await this._createTokenWithTimeout(30000);
            console.log("[Clover Terminal] createToken result:", tokenResult);
        } catch (e) {
            console.error("[Clover Terminal] createToken failed:", e);
            this.state.step = "form";
            const hint = e.message && e.message.includes("timed out")
                ? _t(
                    "Card tokenization timed out. This usually means the " +
                    "PAKMS key does not match the environment (sandbox vs " +
                    "production). Please verify your Clover credentials " +
                    "under Payment Providers."
                  )
                : _t("Failed to tokenize card: ") + (e.message || "");
            this.notification.add(hint, {type: "danger"});
            return;
        }

        if (tokenResult.errors) {
            this.state.step = "form";
            const msg = Object.values(tokenResult.errors).join(", ");
            this.notification.add(_t("Card error: ") + msg, {type: "danger"});
            return;
        }

        if (!tokenResult.token) {
            this.state.step = "form";
            this.notification.add(
                _t("No token received from Clover. Please re-enter card details."),
                {type: "danger"},
            );
            return;
        }

        // Send to server
        try {
            const result = await rpc("/payment/clover/terminal/process", {
                provider_id: this.state.selectedProviderId,
                amount: amount,
                currency_id: this.state.selectedCurrencyId,
                partner_id: partnerId,
                clover_token: tokenResult.token,
                description: this.state.description,
            });

            this.state.step = "result";
            this.state.resultStatus = result.status;
            this.state.resultReference = result.reference || "";
            this.state.resultMessage = result.message || (
                result.status === "ok"
                    ? _t("Payment processed successfully!")
                    : _t("Payment failed.")
            );
        } catch (e) {
            this.state.step = "result";
            this.state.resultStatus = "error";
            this.state.resultMessage = e.message || _t("An error occurred.");
        }
    }

    resetForm() {
        this.state.step = "form";
        this.state.amount = "";
        this.state.description = "";
        this.state.selectedPartnerId = null;
        this.state.partnerSearch = "";
        this.state.selectedItemId = null;
        this.state.isGuest = false;
        this.state.resultStatus = "";
        this.state.resultMessage = "";
        this.state.resultReference = "";
        this.state.cardReady = false;
        this.state.cardError = "";
        this._destroyClover();

        // Re-initialize card reader after DOM renders
        if (this.state.selectedProviderId) {
            setTimeout(() => this.initCloverIframe(), 300);
        }
    }

    _destroyClover() {
        this._cloverInstance = null;
        this._cloverElements = {};

        // Remove the Clover SDK script tag so it doesn't inject persistent
        // DOM elements (e.g. "Powered by Clover" footer) across the SPA.
        const scripts = document.querySelectorAll(
            'script[src*="clover.com/sdk.js"]'
        );
        scripts.forEach(s => s.remove());

        // Remove any Clover-injected branding / overlay elements
        document.querySelectorAll(
            '.clover-footer, [id*="clover-footer"], [class*="clover-badge"]'
        ).forEach(el => el.remove());

        // Also remove the global Clover constructor so a fresh SDK can load.
        // Note: the Clover SDK may set window.Clover as non-configurable,
        // so `delete` would throw a TypeError.  Setting to undefined is safe.
        try {
            delete window.Clover;
        } catch (_e) {
            window.Clover = undefined;
        }
    }

    _loadScript(src) {
        return new Promise((resolve, reject) => {
            if (document.querySelector(`script[src="${src}"]`)) {
                resolve();
                return;
            }
            const script = document.createElement("script");
            script.src = src;
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }
}

registry.category("actions").add("payment_clover.terminal", CloverPaymentTerminal);
