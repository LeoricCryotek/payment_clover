/** @odoo-module **/

/**
 * Clover inline payment form handler.
 *
 * Loads the Clover iframe SDK, mounts card input elements, and on
 * form submission tokenises the card and sends the token to the Odoo
 * controller which creates the charge.
 *
 * Works with the standard Odoo payment form lifecycle:
 *   1. ``_prepareInlineForm()``  — called when provider is selected
 *   2. ``_processPayment()``     — called when "Pay Now" is clicked
 */

import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.PaymentCloverForm = publicWidget.Widget.extend({
    selector: '#payment_method',
    events: {},

    /**
     * @override
     */
    start() {
        this._super(...arguments);
        this._cloverInstance = null;
        this._cloverElements = {};
        this._cloverReady = false;
        // Listen for provider selection changes
        this._onProviderSelected();
        return Promise.resolve();
    },

    /**
     * Watch for when the Clover provider is selected and initialise
     * the iframe elements.
     */
    _onProviderSelected() {
        const form = this.el.closest('form') || this.el;
        const observer = new MutationObserver(() => {
            const container = document.getElementById('clover-payment-form');
            if (container && !this._cloverReady) {
                this._initClover(container);
            }
        });
        observer.observe(form, {childList: true, subtree: true});

        // Also try immediately
        const container = document.getElementById('clover-payment-form');
        if (container && !this._cloverReady) {
            this._initClover(container);
        }
    },

    /**
     * Load the Clover SDK script and mount card elements.
     */
    async _initClover(container) {
        const pakmsKey = container.dataset.cloverPakmsKey;
        const sdkUrl = container.dataset.cloverSdkUrl;

        if (!pakmsKey || !sdkUrl) {
            console.error('Clover: missing PAKMS key or SDK URL');
            return;
        }

        // Load SDK if not already loaded
        if (!window.Clover) {
            await this._loadScript(sdkUrl);
        }

        if (!window.Clover) {
            console.error('Clover SDK failed to load');
            this._showError('Could not load the Clover payment SDK.');
            return;
        }

        try {
            this._cloverInstance = new window.Clover(pakmsKey);
            const elements = this._cloverInstance.elements();

            // Create individual card elements
            this._cloverElements.cardNumber = elements.create('CARD_NUMBER');
            this._cloverElements.cardDate = elements.create('CARD_DATE');
            this._cloverElements.cardCvv = elements.create('CARD_CVV');
            this._cloverElements.cardPostal = elements.create('CARD_POSTAL_CODE');

            // Mount them
            this._cloverElements.cardNumber.mount('#clover-card-number');
            this._cloverElements.cardDate.mount('#clover-card-date');
            this._cloverElements.cardCvv.mount('#clover-card-cvv');
            this._cloverElements.cardPostal.mount('#clover-card-postal');

            this._cloverReady = true;
        } catch (e) {
            console.error('Clover init error:', e);
            this._showError('Failed to initialize Clover payment form.');
        }
    },

    /**
     * Dynamically load an external script.
     */
    _loadScript(src) {
        return new Promise((resolve, reject) => {
            if (document.querySelector(`script[src="${src}"]`)) {
                resolve();
                return;
            }
            const script = document.createElement('script');
            script.src = src;
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
    },

    /**
     * Display an error message in the card errors div.
     */
    _showError(message) {
        const errDiv = document.getElementById('clover-card-errors');
        if (errDiv) {
            errDiv.textContent = message;
        }
    },

    /**
     * Clear any displayed error.
     */
    _clearError() {
        const errDiv = document.getElementById('clover-card-errors');
        if (errDiv) {
            errDiv.textContent = '';
        }
    },
});


/**
 * Extend the payment form's processing to handle Clover tokenization.
 *
 * This hooks into the Odoo payment form's standard flow: when the user
 * clicks "Pay Now" and the selected provider is Clover, we intercept,
 * tokenise via the Clover SDK, then send the token to our controller.
 */
publicWidget.registry.PaymentForm?.include?.({

    /**
     * Override to handle Clover's inline form.
     */
    async _processProviderPayment(providerCode, providerId, processingValues) {
        if (providerCode !== 'clover') {
            return this._super(...arguments);
        }

        // Verify Clover is ready
        const cloverWidget = this.__parentedChildren?.find(
            w => w instanceof publicWidget.registry.PaymentCloverForm
        );

        const container = document.getElementById('clover-payment-form');
        if (!container) {
            this._displayError(_t("Payment Error"),
                _t("Clover payment form not found."));
            return;
        }

        // Get the Clover instance from the global scope
        const pakmsKey = container.dataset.cloverPakmsKey;
        let cloverInstance;
        try {
            cloverInstance = new window.Clover(pakmsKey);
        } catch (e) {
            this._displayError(_t("Payment Error"),
                _t("Could not initialize Clover SDK."));
            return;
        }

        // Tokenize the card
        let tokenResult;
        try {
            tokenResult = await cloverInstance.createToken();
        } catch (e) {
            this._displayError(_t("Payment Error"),
                _t("Could not process card details."));
            return;
        }

        if (tokenResult.errors) {
            const errorMessages = Object.values(tokenResult.errors).join(', ');
            this._displayError(_t("Card Error"), errorMessages);
            return;
        }

        if (!tokenResult.token) {
            this._displayError(_t("Payment Error"),
                _t("No token received from Clover."));
            return;
        }

        // Send token to our controller to create the charge
        try {
            const result = await rpc(
                processingValues.return_url || '/payment/clover/return',
                {
                    reference: processingValues.reference,
                    clover_token: tokenResult.token,
                },
            );

            if (result.status === 'ok') {
                // Redirect to the payment status page
                window.location.href = '/payment/status';
            } else {
                this._displayError(
                    _t("Payment Failed"),
                    result.message || _t("The payment could not be processed."),
                );
            }
        } catch (e) {
            this._displayError(_t("Payment Error"),
                _t("An error occurred while processing the payment."));
        }
    },
});
