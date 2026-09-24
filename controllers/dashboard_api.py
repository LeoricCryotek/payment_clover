# -*- coding: utf-8 -*-
"""JSON endpoints backing the Clover Live Dashboard OWL widget.

The client component posts a date range and expects back KPI totals
(with prior-period deltas for arrow indicators) and top-N lists
for products / employees / customers. All heavy lifting is done
via ``read_group`` so PostgreSQL aggregates the numbers server-side
— we never load individual clover.sale rows into memory.

When the environment has no ``clover.sale`` rows at all (fresh
install, sandbox database, demo screen) the endpoints return a
plausible synthetic dataset so the dashboard shows meaningful
tiles + tables instead of an empty shell. The synthetic response
carries ``is_demo: true`` so the UI can flag "Demo Data".

Access control is via the ``payment_clover.group_clover_user``
group (checked with ``sudo()`` only after the group check passes),
which matches every other Clover reporting menu.
"""
import random
from datetime import datetime, timedelta

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request


def _ensure_clover_access(env):
    user = env.user
    if not user.has_group("payment_clover.group_clover_user"):
        raise AccessError("Clover access is required for this view.")


# --- Mock-data helpers ------------------------------------------

# Small deterministic seed so the demo numbers are stable within
# a single browser session (rand.seed based on ISO day + period),
# but vary from day to day so screenshots look natural.
def _seeded_rand(*keys):
    seed = "|".join(str(k) for k in keys)
    rand = random.Random()
    rand.seed(seed)
    return rand


DEMO_PRODUCTS = [
    "Well Rum", "Bud Light Draft", "Coors Light 12oz",
    "Michelob Ultra", "House Cabernet 6oz", "Jameson Rocks",
    "Cheeseburger", "Chicken Wings (10)", "Onion Rings",
    "Chicken Strips",
]
DEMO_EMPLOYEES = [
    "Aaron Schumacher", "Amanda Stucky", "Danielle Sauve",
    "Danny Santiago", "David Gregory", "Kelly Beach",
    "Maggie Hazelbaker", "Jon Longtin",
]
DEMO_CUSTOMERS = [
    "Walk-in", "Dalton Chatfield", "Nick Phillips",
    "Doug Ruesch", "Paula Earl", "Carson Heschle",
    "Rochelle Holman",
]


def _mock_totals(start, end, magnitude):
    """Build a plausible totals dict for a given date range."""
    days = max(1, (end - start).total_seconds() / 86400.0)
    r = _seeded_rand("totals", start.isoformat(), end.isoformat())
    daily_gross = r.uniform(400, 900) * magnitude
    gross = daily_gross * days
    # Bar/lodge typical: COGS ~ 28-34% of gross for food/bev mix
    cogs = gross * r.uniform(0.28, 0.34)
    net = gross - cogs
    tips = gross * r.uniform(0.14, 0.18)
    orders = int(round(gross / r.uniform(22, 34)))
    return {
        "gross": round(gross, 2),
        "cogs": round(cogs, 2),
        "net": round(net, 2),
        "tips": round(tips, 2),
        "orders": orders,
        "avg_ticket": round(gross / orders, 2) if orders else 0.0,
    }


def _mock_top_products(start, end, limit):
    r = _seeded_rand("top-products", start.isoformat())
    picks = r.sample(DEMO_PRODUCTS, min(limit, len(DEMO_PRODUCTS)))
    rows = []
    for i, name in enumerate(picks):
        qty = r.randint(40 - i * 3, 180 - i * 10)
        revenue = qty * r.uniform(4.50, 12.00)
        margin = revenue * r.uniform(0.55, 0.72)
        rows.append({
            "id": 0, "name": name, "qty": qty,
            "amount": round(revenue, 2),
            "margin": round(margin, 2),
        })
    rows.sort(key=lambda x: -x["amount"])
    return rows


def _mock_top_employees(start, end, limit):
    r = _seeded_rand("top-employees", start.isoformat())
    picks = r.sample(DEMO_EMPLOYEES, min(limit, len(DEMO_EMPLOYEES)))
    rows = []
    for i, name in enumerate(picks):
        amount = r.uniform(1500 - i * 130, 3200 - i * 200)
        tips = amount * r.uniform(0.12, 0.19)
        net = amount * r.uniform(0.62, 0.72)
        rows.append({
            "id": 0, "name": name,
            "amount": round(amount, 2),
            "tips": round(tips, 2),
            "net": round(net, 2),
        })
    rows.sort(key=lambda x: -x["amount"])
    return rows


def _mock_top_customers(start, end, limit):
    r = _seeded_rand("top-customers", start.isoformat())
    picks = r.sample(DEMO_CUSTOMERS, min(limit, len(DEMO_CUSTOMERS)))
    rows = []
    # Walk-in dominates in a lodge / bar
    walk_in_amt = r.uniform(6000, 12000)
    for i, name in enumerate(picks):
        amount = (walk_in_amt if name == "Walk-in"
                  else r.uniform(50 - i * 3, 250 - i * 10))
        net = amount * r.uniform(0.62, 0.72)
        rows.append({
            "name": name,
            "amount": round(amount, 2),
            "net": round(net, 2),
        })
    rows.sort(key=lambda x: -x["amount"])
    return rows


def _clover_data_exists(env):
    """True when the current DB has at least one clover.sale row."""
    return bool(env["clover.sale"].sudo().search_count([], limit=1))


def _parse_iso(dtstr, default):
    if not dtstr:
        return default
    try:
        # Accept both `2026-09-22T00:00:00.000Z` (from JS toISOString)
        # and `2026-09-22 00:00:00` (from datetime.isoformat).
        cleaned = dtstr.replace("Z", "").replace("T", " ").split(".")[0]
        return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return default


class CloverDashboardApi(http.Controller):

    @http.route(
        "/clover/dashboard/kpis",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def kpis(self, start_date=None, end_date=None):
        env = request.env
        _ensure_clover_access(env)

        now = datetime.now()
        end = _parse_iso(end_date, now)
        start = _parse_iso(
            start_date, now - timedelta(hours=24))

        # Previous period of the same length, ending where this
        # one starts. Powers the "vs prior period" delta.
        span = end - start
        prev_end = start
        prev_start = start - span

        # No Clover rows in this DB → return synthetic demo data
        # so the dashboard shows meaningful tiles instead of an
        # empty shell.
        if not _clover_data_exists(env):
            cur = _mock_totals(start, end, magnitude=1.0)
            prev = _mock_totals(
                prev_start, prev_end,
                magnitude=0.85 + _seeded_rand(
                    "prev-mag", start.isoformat()).random() * 0.3,
            )
            return {
                "is_demo": True,
                "range": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "prev_start": prev_start.isoformat(),
                    "prev_end": prev_end.isoformat(),
                },
                "current": cur,
                "previous": prev,
            }

        Sale = env["clover.sale"].sudo()

        def _totals(dstart, dend):
            domain = [("date", ">=", dstart), ("date", "<", dend)]
            rows = Sale.read_group(
                domain,
                ["total_amount:sum", "cost_amount:sum",
                 "net_amount:sum", "tip_amount:sum"],
                [],
            )
            row = rows[0] if rows else {}
            count = Sale.search_count(domain)
            gross = row.get("total_amount") or 0.0
            return {
                "gross": gross,
                "cogs": row.get("cost_amount") or 0.0,
                "net": row.get("net_amount") or 0.0,
                "tips": row.get("tip_amount") or 0.0,
                "orders": count,
                "avg_ticket": (gross / count) if count else 0.0,
            }

        cur = _totals(start, end)
        prev = _totals(prev_start, prev_end)
        return {
            "is_demo": False,
            "range": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "prev_start": prev_start.isoformat(),
                "prev_end": prev_end.isoformat(),
            },
            "current": cur,
            "previous": prev,
        }

    @http.route(
        "/clover/dashboard/top",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def top(self, kind="products", start_date=None,
            end_date=None, limit=10):
        env = request.env
        _ensure_clover_access(env)

        now = datetime.now()
        end = _parse_iso(end_date, now)
        start = _parse_iso(
            start_date, now - timedelta(days=30))
        limit = max(1, min(int(limit or 10), 50))

        domain_date = [("date", ">=", start), ("date", "<", end)]

        # No Clover rows → synthetic demo data.
        if not _clover_data_exists(env):
            if kind == "products":
                return _mock_top_products(start, end, limit)
            if kind == "employees":
                return _mock_top_employees(start, end, limit)
            if kind == "customers":
                return _mock_top_customers(start, end, limit)
            return []

        if kind == "products":
            Line = env["clover.sale.line"].sudo()
            groups = Line.read_group(
                domain_date + [("product_id", "!=", False)],
                ["product_id", "unit_qty:sum", "amount:sum",
                 "margin_amount:sum"],
                ["product_id"],
                limit=limit,
                orderby="amount desc",
            )
            return [{
                "id": g["product_id"][0] if g["product_id"] else 0,
                "name": (g["product_id"][1]
                         if g["product_id"] else "Unknown"),
                "qty": g.get("unit_qty") or 0,
                "amount": g.get("amount") or 0.0,
                "margin": g.get("margin_amount") or 0.0,
            } for g in groups]

        if kind == "employees":
            Sale = env["clover.sale"].sudo()
            groups = Sale.read_group(
                domain_date + [("employee_id", "!=", False)],
                ["employee_id", "total_amount:sum",
                 "tip_amount:sum", "net_amount:sum"],
                ["employee_id"],
                limit=limit,
                orderby="total_amount desc",
            )
            return [{
                "id": (g["employee_id"][0]
                       if g["employee_id"] else 0),
                "name": (g["employee_id"][1]
                         if g["employee_id"] else "Unknown"),
                "amount": g.get("total_amount") or 0.0,
                "tips": g.get("tip_amount") or 0.0,
                "net": g.get("net_amount") or 0.0,
            } for g in groups]

        if kind == "customers":
            Sale = env["clover.sale"].sudo()
            groups = Sale.read_group(
                domain_date,
                ["customer_display_name", "total_amount:sum",
                 "net_amount:sum"],
                ["customer_display_name"],
                limit=limit,
                orderby="total_amount desc",
            )
            return [{
                "name": g.get("customer_display_name") or "Walk-in",
                "amount": g.get("total_amount") or 0.0,
                "net": g.get("net_amount") or 0.0,
            } for g in groups]

        return []
