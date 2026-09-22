# -*- coding: utf-8 -*-
"""Defensive patch for Odoo 19 board.board drag-and-drop crash.

Odoo 19's board.board dashboard JS calls the ``View.edit_custom()``
HTTP controller whenever a user drags a dashboard widget. In this
build the JS omits the required ``custom_id`` argument and the
server 500s:

    TypeError: View.edit_custom() missing 1 required positional
    argument: 'custom_id'

Fix: three overlapping mechanisms, so whichever survives Odoo's
route-caching lifecycle wins.

  1. Monkey-patch the base ``View.edit_custom`` in-place — swaps
     the function object on the class dict. Effective if Odoo's
     router does per-request ``getattr(cls, name)``.

  2. Re-decorate a subclass method with ``@http.route()`` so
     Odoo's controller registry sees a NEW routed method for the
     same URL. Odoo's route builder walks Controller.__subclasses__
     and later declarations override earlier ones for the same
     route.

  3. Copy the parent's route metadata onto the wrapper function
     directly so the router still recognises it as routable even
     when the decorator inheritance path doesn't fire.

All three run at module-load time. Failure of any single layer is
logged but non-fatal; the module keeps loading.
"""
import functools
import logging

_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Resolve base View controller (path moved between Odoo minors).
# ------------------------------------------------------------------
_View = None
_View_origin = None
try:
    from odoo.addons.web.controllers.view import View as _View
    _View_origin = "web.controllers.view"
except ImportError:
    try:
        from odoo.addons.web.controllers.main import View as _View
        _View_origin = "web.controllers.main"
    except ImportError:
        pass


def _tolerant_edit_custom(original):
    """Return a wrapped edit_custom that no-ops on missing args."""

    def edit_custom(self, custom_id=None, arch=None):
        if custom_id is None or arch is None:
            _logger.info(
                "payment_clover: swallowed board.board edit_custom "
                "call with custom_id=%r arch_present=%s "
                "(Odoo 19 dashboard drag-and-drop bug).",
                custom_id, arch is not None,
            )
            return True
        return original(self, custom_id, arch)

    # Preserve identity (name, docstring, __wrapped__).
    functools.update_wrapper(edit_custom, original)
    # Make the args truly optional at the Python level too.
    edit_custom.__defaults__ = (None, None)
    # Copy Odoo-specific routing metadata onto the wrapper.
    for attr in ("routing", "original_routing", "original_func",
                 "route_data"):
        if hasattr(original, attr):
            try:
                setattr(edit_custom, attr, getattr(original, attr))
            except (AttributeError, TypeError):
                pass
    return edit_custom


if _View is None:
    _logger.warning(
        "payment_clover: could not locate the web View controller "
        "class — board.board drag-and-drop crash workaround is "
        "NOT installed. The rest of the module is unaffected.",
    )

else:
    _original_ec = _View.edit_custom
    _wrapped_ec = _tolerant_edit_custom(_original_ec)

    # --- Layer 1: monkey-patch base class attribute. ---
    if not getattr(_View, "_clover_edit_custom_patched", False):
        _View.edit_custom = _wrapped_ec
        _View._clover_edit_custom_patched = True
        _logger.info(
            "payment_clover: monkey-patched %s.View.edit_custom.",
            _View_origin,
        )

    # --- Layer 2: subclass declaration for the router. ---
    # We re-declare with @http.route() so Odoo's route registry
    # picks up the subclassed method as a fresh routed endpoint
    # for the SAME URL. Odoo 15+ resolves duplicate-URL routes by
    # taking the last-loaded subclass — we load after `web` and
    # `board`, so ours wins.
    try:
        from odoo import http

        # Reconstruct the route decorator kwargs from the original
        # method's `routing` dict (populated by @http.route()).
        _routing = getattr(_original_ec, "routing", None)
        if isinstance(_routing, dict) and _routing.get("routes"):

            class CloverPatchedView(_View):

                @http.route(
                    _routing["routes"],
                    type=_routing.get("type", "json"),
                    auth=_routing.get("auth", "user"),
                    methods=_routing.get("methods"),
                    cors=_routing.get("cors"),
                    csrf=_routing.get("csrf", True),
                    save_session=_routing.get("save_session", True),
                    readonly=_routing.get("readonly", False),
                )
                def edit_custom(self, custom_id=None, arch=None):
                    if custom_id is None or arch is None:
                        _logger.info(
                            "payment_clover: (subclass) swallowed "
                            "board.board edit_custom bug call.",
                        )
                        return True
                    return super().edit_custom(custom_id, arch)

            _logger.info(
                "payment_clover: registered CloverPatchedView with "
                "@http.route(%s) for edit_custom.",
                _routing["routes"],
            )
        else:
            _logger.info(
                "payment_clover: no `routing` metadata on the base "
                "edit_custom — skipping subclass re-declaration "
                "layer; monkey-patch layer is still active.",
            )
    except Exception as e:
        _logger.warning(
            "payment_clover: subclass layer for edit_custom "
            "failed: %s. Monkey-patch layer is still active.", e,
        )
