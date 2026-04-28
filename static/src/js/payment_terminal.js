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
            ["id", "name", "clover_pakms_key", "state"],
        );
        this.state.providers = providers;
        if (providers.length === 1) {
            this.state.selectedProviderId = providers[0].id;
        }

        // Load currencies
        const currencies = await this.orm.searchRead(
            "res.currency",
            [["name", "in", ["USD", "CAD", "GBP", "EUR"]], ["active", "=", true]],
            ["id", "name", "symbol"],
        );
        this.state.currencies = currencies;
        if (currencies.length > 0) {
            const usd = currencies.find(c => c.name === "USD");
            this.state.selectedCurrencyId = usd ? usd.id : currencies[0].id;
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
    }

    async initCloverIframe() {
        if (this._cloverInstance) return;

        const provider = this.state.providers.find(
            p => p.id === this.state.selectedProviderId
        );
        if (!provider || !provider.clover_pakms_key) {
            this.notification.add(_t("Provider missing PAKMS key."), {type: "danger"});
            return;
        }

        // Determine SDK URL based on provider state
        const sdkUrl = provider.state === "test"
            ? "https://checkout.sandbox.dev.clover.com/sdk.js"
            : "https://checkout.clover.com/sdk.js";

        // Load SDK
        if (!window.Clover) {
            await this._loadScript(sdkUrl);
        }
        if (!window.Clover) {
            this.notification.add(_t("Failed to load Clover SDK."), {type: "danger"});
            return;
        }

        this._cloverInstance = new window.Clover(provider.clover_pakms_key);
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
        if (!this.state.selectedPartnerId) {
            this.notification.add(_t("Please select a customer."), {type: "warning"});
            return;
        }
        if (!this._cloverInstance) {
            this.notification.add(_t("Card form not ready. Please wait."), {type: "warning"});
            return;
        }

        this.state.step = "processing";

        // Tokenize
        let tokenResult;
        try {
            tokenResult = await this._cloverInstance.createToken();
        } catch (e) {
            this.state.step = "form";
            this.notification.add(
                _t("Failed to tokenize card: ") + (e.message || ""),
                {type: "danger"},
            );
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
                partner_id: this.state.selectedPartnerId,
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
        this.state.resultStatus = "";
        this.state.resultMessage = "";
        this.state.resultReference = "";
        this._destroyClover();
    }

    _destroyClover() {
        // Clover elements don't have a destroy method; just null refs
        this._cloverInstance = null;
        this._cloverElements = {};
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
