# -*- coding: utf-8 -*-
# Part of Elks Lodge Odoo Modules. See LICENSE file for full copyright and licensing details.

"""Constants for the Clover payment provider."""

# Clover API base URLs
API_URLS = {
    'sandbox': {
        'ecommerce': 'https://scl-sandbox.dev.clover.com',
        'platform': 'https://apisandbox.dev.clover.com',
        'tokenizer': 'https://token-sandbox.dev.clover.com',
        'iframe_sdk': 'https://checkout.sandbox.dev.clover.com/sdk.js',
    },
    'production': {
        'ecommerce': 'https://scl.clover.com',
        'platform': 'https://api.clover.com',
        'tokenizer': 'https://token.clover.com',
        'iframe_sdk': 'https://checkout.clover.com/sdk.js',
    },
}

# Map Odoo payment states to Clover charge statuses
STATUS_MAPPING = {
    'pending': {'pending'},
    'done': {'succeeded', 'paid'},
    'cancel': {'canceled'},
    'error': {'failed', 'declined'},
}

# Default payment method codes supported by Clover
DEFAULT_PAYMENT_METHOD_CODES = {'card'}

# Sensitive keys to mask in logs
SENSITIVE_KEYS = {
    'clover_api_key',
    'clover_pakms_key',
    'source',
    'cvv',
    'number',
}

# Clover webhook event types we handle
HANDLED_WEBHOOK_EVENTS = [
    'CHARGE.CREATED',
    'CHARGE.CAPTURED',
    'CHARGE.FAILED',
    'REFUND.CREATED',
]

# Supported currencies — Elks lodges are US-only
SUPPORTED_CURRENCIES = {'USD'}

# Test card numbers for sandbox
TEST_CARDS = {
    'visa_success': '4111111111111111',
    'visa_decline': '4012888888881881',
    'mastercard': '5500000000000004',
    'amex': '378282246310005',
    'discover': '6011111111111117',
}
