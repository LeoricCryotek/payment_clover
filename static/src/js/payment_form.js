/** @odoo-module **/
/* global Clover */

/**
 * Clover inline payment form handler (Odoo 19).
 *
 * Mirrors the payment_authorize interaction pattern:
 *
 *   1. ``_prepareInlineForm()``  — called when the Clover radio is
 *      picked. We call ``_setPaymentFlow('direct')`` so Odoo routes to
 *      ``_processDirectFlow`` instead of the default redirect flow (the
 *      redirect flow crashes on Clover because we have no redirect DOM).
 *      Also loads the Clover hosted-iframe SDK and mounts the card
 *      elements (number, date, cvv, postal).
 *
 *   2. ``_processDirectFlow()`` — called when the customer clicks
 *      "Pay Now". Tokenises the card via the Clover SDK, POSTs the
 *      token to our controller (``/payment/clover/return``) which
 *      creates the charge, then redirects to ``/payment/status``.
 *
 * Do NOT use the legacy ``publicWidget.registry.PaymentForm.include``
 * pattern in Odoo 19 — the payment form was moved to the interactions
 * system and the legacy registry entry no longer exists, so any
 * overrides on it silently do nothing.
 */

import { loadJS } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { rpc, RPCError } from "@web/core/network/rpc";
import { patch } from "@web/core/utils/patch";

import { PaymentForm } from "@payment/interactions/payment_form";

patch(PaymentForm.prototype, {

    setup() {
        super.setup();
        // Per-payment-option cache so we don't re-instantiate the
        // Clover SDK / re-mount iframes on every radio click.
        this.cloverData = {};
    },

    // #=== DOM MANIPULATION ===#

    /**
     * Prepare the Clover inline form for a direct-flow tokenisation.
     *
     * @override
     */
    async _prepareInlineForm(providerId, providerCode, paymentOptionId,
                             paymentMethodCode, flow) {
        if (providerCode !== "clover") {
            await super._prepareInlineForm(...arguments);
            return;
        }
        // Tokenised (saved-card) payments use the generic flow.
        if (flow === "token") {
            return;
        }

        // Switch the selected payment method to Odoo's "direct" flow so
        // that _processDirectFlow (below) is invoked on submit rather
        // than _processRedirectFlow (which has no redirect payload for
        // Clover and errors out).
        this._setPaymentFlow("direct");

        // Locate the Clover inline form container inside the radio's
        // inline form area. The template renders a div#clover-payment-
        // form with data-* attributes carrying pakms/merchant/sdk info.
        const radio = document.querySelector(
            'input[name="o_payment_radio"]:checked');
        const inlineForm = this._getInlineForm(radio);
        if (!inlineForm) {
            return;
        }
        const cloverForm = inlineForm.querySelector("#clover-payment-form");
        if (!cloverForm) {
            return;
        }

        const pakmsKey = cloverForm.dataset.cloverPakmsKey;
        const merchantId = cloverForm.dataset.cloverMerchantId;
        const sdkUrl = cloverForm.dataset.cloverSdkUrl;

        if (!pakmsKey || !sdkUrl) {
            console.error(
                "Clover: missing PAKMS key or SDK URL on inline form.");
            return;
        }

        // Load the Clover hosted-iframe SDK once per page.
        if (!window.Clover) {
            await loadJS(sdkUrl);
        }
        if (!window.Clover) {
            console.error("Clover: SDK failed to load from", sdkUrl);
            return;
        }

        // Instantiate + mount elements once per payment option.
        if (this.cloverData[paymentOptionId]) {
            return;
        }
        const cloverOpts = {};
        if (merchantId) {
            cloverOpts.merchantId = merchantId;
        }
        let cloverInstance;
        try {
            cloverInstance = new window.Clover(pakmsKey, cloverOpts);
        } catch (e) {
            console.error("Clover: SDK init failed:", e);
            return;
        }
        const elements = cloverInstance.elements();

        // Mount into the four div slots defined in the template.
        // The Clover SDK's .mount() takes a CSS selector STRING, not
        // an HTMLElement — passing the element throws
        // "[object HTMLDivElement] is not a valid selector".
        const numberEl = elements.create("CARD_NUMBER");
        const dateEl = elements.create("CARD_DATE");
        const cvvEl = elements.create("CARD_CVV");
        const postalEl = elements.create("CARD_POSTAL_CODE");

        numberEl.mount("#clover-card-number");
        dateEl.mount("#clover-card-date");
        cvvEl.mount("#clover-card-cvv");
        postalEl.mount("#clover-card-postal");

        this.cloverData[paymentOptionId] = {
            instance: cloverInstance,
            elements: {
                number: numberEl,
                date: dateEl,
                cvv: cvvEl,
                postal: postalEl,
            },
            form: cloverForm,
        };
    },

    // #=== PAYMENT FLOW ===#

    /**
     * Process the Clover direct payment: tokenise, POST to controller,
     * redirect to /payment/status.
     *
     * @override
     */
    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode,
                             processingValues) {
        if (providerCode !== "clover") {
            await super._processDirectFlow(...arguments);
            return;
        }

        const data = this.cloverData[paymentOptionId];
        if (!data || !data.instance) {
            this._displayErrorDialog(
                _t("Payment processing failed"),
                _t("The Clover payment form is not ready. Please refresh " +
                    "the page and try again."),
            );
            this._enableButton?.();
            return;
        }

        // Tokenise the card via the Clover SDK.
        let tokenResult;
        try {
            tokenResult = await data.instance.createToken();
        } catch (e) {
            this._displayErrorDialog(
                _t("Payment processing failed"),
                _t("Could not read card details: %s", e.message || ""),
            );
            this._enableButton?.();
            return;
        }

        if (tokenResult && tokenResult.errors) {
            const msg = Object.values(tokenResult.errors).join(", ");
            this._displayErrorDialog(_t("Card error"), msg);
            this._enableButton?.();
            return;
        }
        if (!tokenResult || !tokenResult.token) {
            this._displayErrorDialog(
                _t("Payment processing failed"),
                _t("No payment token was returned by Clover."),
            );
            this._enableButton?.();
            return;
        }

        // POST the single-use token to our controller. It creates the
        // Clover order + charge server-side and updates the tx state.
        try {
            const result = await rpc("/payment/clover/return", {
                reference: processingValues.reference,
                clover_token: tokenResult.token,
            });
            if (result && result.status === "ok") {
                window.location = "/payment/status";
                return;
            }
            this._displayErrorDialog(
                _t("Payment failed"),
                (result && result.message)
                    || _t("The payment could not be processed."),
            );
            this._enableButton?.();
        } catch (error) {
            if (error instanceof RPCError) {
                this._displayErrorDialog(
                    _t("Payment processing failed"),
                    error.data?.message || error.message || "",
                );
            } else {
                this._displayErrorDialog(
                    _t("Payment processing failed"),
                    error.message || _t("Unexpected error."),
                );
            }
            this._enableButton?.();
        }
    },
});
