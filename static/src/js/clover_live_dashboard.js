/** @odoo-module **/
/**
 * Clover Live Dashboard — an OWL client action that renders KPI
 * tiles + top-N tables + a period picker for the Clover sales
 * mirror. Registered as `payment_clover.live_dashboard` in the
 * actions registry and wired to a menuitem in the manifest.
 *
 * Data comes from /clover/dashboard/kpis and /clover/dashboard/top
 * (see controllers/dashboard_api.py) — both are POST-JSON, both
 * take an ISO date range, both aggregate on the server so the
 * browser only sees pre-computed totals.
 *
 * State model:
 *   - `period`      : string key of the active preset button.
 *   - `startDate`   : Date instance, inclusive lower bound.
 *   - `endDate`     : Date instance, exclusive upper bound.
 *   - `kpis`        : object with `current`, `previous`, `range`.
 *   - `top{Products,Employees,Customers}` : arrays of row dicts.
 *   - `loading`     : boolean shown as a translucent overlay.
 *
 * Every user click (period button, prev/next-month arrow) calls
 * `refresh()` which reruns the two RPCs in parallel.
 */
import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";

function startOfDay(d) {
    const o = new Date(d);
    o.setHours(0, 0, 0, 0);
    return o;
}
function endOfDay(d) {
    const o = new Date(d);
    o.setHours(23, 59, 59, 999);
    return o;
}
function startOfMonth(d) {
    return new Date(d.getFullYear(), d.getMonth(), 1);
}
function endOfMonth(d) {
    return new Date(d.getFullYear(), d.getMonth() + 1,
                    0, 23, 59, 59, 999);
}

const PERIODS = {
    last24h: (now) => ({
        start: new Date(now.getTime() - 24 * 60 * 60 * 1000),
        end: new Date(now),
    }),
    today: (now) => ({
        start: startOfDay(now),
        end: endOfDay(now),
    }),
    yesterday: (now) => {
        const y = new Date(now);
        y.setDate(y.getDate() - 1);
        return { start: startOfDay(y), end: endOfDay(y) };
    },
    last_7_days: (now) => ({
        start: startOfDay(new Date(now.getTime()
                                   - 7 * 24 * 60 * 60 * 1000)),
        end: endOfDay(now),
    }),
    this_month: (now) => ({
        start: startOfMonth(now),
        end: endOfMonth(now),
    }),
    last_month: (now) => {
        const lm = new Date(now.getFullYear(),
                            now.getMonth() - 1, 1);
        return { start: startOfMonth(lm), end: endOfMonth(lm) };
    },
    ytd: (now) => ({
        start: new Date(now.getFullYear(), 0, 1),
        end: endOfDay(now),
    }),
};

class CloverLiveDashboard extends Component {
    static template = "payment_clover.CloverLiveDashboard";
    static props = ["*"];

    setup() {
        const now = new Date();
        const range = PERIODS.last24h(now);
        this.state = useState({
            period: "last24h",
            startDate: range.start,
            endDate: range.end,
            kpis: null,
            topProducts: [],
            topEmployees: [],
            topCustomers: [],
            loading: true,
        });
        onWillStart(() => this.refresh());
    }

    async refresh() {
        this.state.loading = true;
        const payload = {
            start_date: this.state.startDate.toISOString(),
            end_date: this.state.endDate.toISOString(),
        };
        try {
            const [kpis, products, employees, customers] =
                await Promise.all([
                    rpc("/clover/dashboard/kpis", payload),
                    rpc("/clover/dashboard/top",
                        { ...payload, kind: "products", limit: 10 }),
                    rpc("/clover/dashboard/top",
                        { ...payload, kind: "employees", limit: 10 }),
                    rpc("/clover/dashboard/top",
                        { ...payload, kind: "customers", limit: 10 }),
                ]);
            this.state.kpis = kpis;
            this.state.topProducts = products || [];
            this.state.topEmployees = employees || [];
            this.state.topCustomers = customers || [];
        } catch (e) {
            console.error("[clover-dashboard] refresh failed:", e);
        } finally {
            this.state.loading = false;
        }
    }

    setPeriod(period) {
        const now = new Date();
        const range = PERIODS[period](now);
        this.state.period = period;
        this.state.startDate = range.start;
        this.state.endDate = range.end;
        this.refresh();
    }

    shiftMonth(offset) {
        const start = new Date(this.state.startDate);
        start.setMonth(start.getMonth() + offset);
        this.state.startDate = startOfMonth(start);
        this.state.endDate = endOfMonth(start);
        // Free-form date range means no preset button is active.
        this.state.period = "custom";
        this.refresh();
    }

    // ---- template helpers ----
    fmt(v) {
        return new Intl.NumberFormat("en-US", {
            style: "currency", currency: "USD",
        }).format(v || 0);
    }

    fmtInt(v) {
        return new Intl.NumberFormat("en-US").format(Math.round(v || 0));
    }

    dateLabel() {
        const s = this.state.startDate;
        const e = this.state.endDate;
        const opts = { month: "short", day: "numeric", year: "numeric" };
        return `${s.toLocaleDateString("en-US", opts)} — ${
            e.toLocaleDateString("en-US", opts)}`;
    }

    delta(cur, prev) {
        cur = cur || 0;
        prev = prev || 0;
        if (!prev && !cur) {
            return { pct: "0.0", dir: "flat", raw: 0 };
        }
        if (!prev) {
            return { pct: "∞", dir: "up", raw: 100 };
        }
        const pct = ((cur - prev) / Math.abs(prev)) * 100;
        return {
            pct: pct.toFixed(1),
            dir: pct > 0.05 ? "up" :
                 (pct < -0.05 ? "down" : "flat"),
            raw: pct,
        };
    }

    dirClass(d) {
        return d.dir === "up" ? "text-success"
             : d.dir === "down" ? "text-danger"
             : "text-muted";
    }

    dirIcon(d) {
        return d.dir === "up" ? "fa-arrow-up"
             : d.dir === "down" ? "fa-arrow-down"
             : "fa-minus";
    }
}

registry.category("actions").add(
    "payment_clover.live_dashboard", CloverLiveDashboard);
