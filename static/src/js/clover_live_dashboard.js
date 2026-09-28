/** @odoo-module **/
/**
 * Clover Live Dashboard — an OWL client action that renders KPI
 * tiles + top-N tables + a period picker for the Clover sales
 * mirror. Registered as `payment_clover.live_dashboard` in the
 * actions registry and wired to a menuitem in the manifest.
 *
 * Data comes from /clover/dashboard/kpis and /clover/dashboard/top
 * (see controllers/dashboard_api.py). Both endpoints return
 * synthetic demo data when the DB has no clover.sale rows, so
 * this dashboard always renders something meaningful.
 *
 * Notes on imports:
 *   - We use `useService("rpc")` instead of the standalone
 *     `rpc` import — the service pattern is stable across every
 *     Odoo 17/18/19 build, whereas the standalone export path
 *     has shifted between point releases.
 *   - `static props = "*"` (string, NOT `["*"]`) tells OWL 2 to
 *     accept any props without validation. The array form is not
 *     a valid props spec and throws during class definition,
 *     which is what took the whole module down previously.
 */
import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";

/**
 * Build an ISO-ish date string suitable for Odoo domain literals
 * ("2026-09-26 12:00:00"). We deliberately format in local time so
 * the domain matches the same window the user selected on the
 * period picker; the server stores clover.sale.date as UTC but
 * Odoo's ORM converts on read.
 */
function toDomainDate(d) {
    const pad = (n) => String(n).padStart(2, "0");
    return (
        d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-"
        + pad(d.getDate()) + " " + pad(d.getHours()) + ":"
        + pad(d.getMinutes()) + ":" + pad(d.getSeconds())
    );
}

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
    static props = "*";

    setup() {
        this.action = useService("action");
        this.notification = useService("notification");
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

    // ---------------------------------------------------------------
    // Click-through
    // ---------------------------------------------------------------

    /**
     * Return the current date range as an Odoo domain fragment.
     * All drill-through views share this range so what the user
     * clicks is what they see.
     */
    _dateDomain(field = "date") {
        return [
            [field, ">=", toDomainDate(this.state.startDate)],
            [field, "<", toDomainDate(this.state.endDate)],
        ];
    }

    /**
     * True in demo mode. Click-through is a no-op there because
     * the numbers on screen are synthetic and don't correspond to
     * any real record.
     */
    _isDemo() {
        return !!(this.state.kpis && this.state.kpis.is_demo);
    }

    _demoNotice() {
        this.notification.add(
            "This dashboard is showing demo data because no Clover " +
            "sales have been synced yet. Drill-through will work " +
            "once real sales arrive.",
            { type: "warning", sticky: false },
        );
    }

    _open({ name, res_model, domain, views, context }) {
        if (this._isDemo()) {
            this._demoNotice();
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model,
            domain,
            views: views || [[false, "list"], [false, "form"]],
            context: context || {},
            target: "current",
        });
    }

    // ---- Tile handlers ----
    openSales() {
        this._open({
            name: "Sales — " + this.dateLabel(),
            res_model: "clover.sale",
            domain: this._dateDomain("date"),
        });
    }

    openNet() {
        // Same list as openSales but the header hints at the metric.
        this._open({
            name: "Net Margin — " + this.dateLabel(),
            res_model: "clover.sale",
            domain: this._dateDomain("date"),
        });
    }

    openTips() {
        // Personal (excludes gratuity / unclaimed / refunded) so
        // the click matches the tile total.
        this._open({
            name: "Tips — " + this.dateLabel(),
            res_model: "clover.tip.entry",
            domain: [
                ...this._dateDomain("date"),
                ["is_event_gratuity", "=", false],
                ["is_unclaimed", "=", false],
                ["is_refunded", "=", false],
            ],
        });
    }

    openOrders() {
        this.openSales();
    }

    openCogs() {
        this._open({
            name: "COGS — " + this.dateLabel(),
            res_model: "clover.sale.line",
            domain: this._dateDomain("date"),
            views: [[false, "list"], [false, "pivot"], [false, "form"]],
        });
    }

    openPriorSales() {
        if (this._isDemo() || !this.state.kpis) {
            this._demoNotice();
            return;
        }
        const r = this.state.kpis.range;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Sales — Prior Period",
            res_model: "clover.sale",
            domain: [
                ["date", ">=", r.prev_start.replace("T", " ")
                                 .substring(0, 19)],
                ["date", "<",  r.prev_end.replace("T", " ")
                                 .substring(0, 19)],
            ],
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    openPriorNet() {
        this.openPriorSales();
    }

    // ---- Row handlers ----
    openProduct(row) {
        if (!row || !row.id) {
            this._demoNotice();
            return;
        }
        this._open({
            name: "Sales of " + row.name + " — " + this.dateLabel(),
            res_model: "clover.sale.line",
            domain: [
                ...this._dateDomain("date"),
                ["product_id", "=", row.id],
            ],
            views: [[false, "list"], [false, "pivot"], [false, "form"]],
        });
    }

    openEmployee(row) {
        if (!row || !row.id) {
            this._demoNotice();
            return;
        }
        this._open({
            name: row.name + " — " + this.dateLabel(),
            res_model: "clover.sale",
            domain: [
                ...this._dateDomain("date"),
                ["employee_id", "=", row.id],
            ],
        });
    }

    openCustomer(row) {
        if (!row || !row.name || row.name === "Walk-in") {
            // Walk-in is an aggregate over sales without a linked
            // partner — filter to those rather than by name.
            if (row && row.name === "Walk-in") {
                this._open({
                    name: "Walk-in Sales — " + this.dateLabel(),
                    res_model: "clover.sale",
                    domain: [
                        ...this._dateDomain("date"),
                        ["partner_id", "=", false],
                    ],
                });
                return;
            }
            this._demoNotice();
            return;
        }
        this._open({
            name: row.name + " — " + this.dateLabel(),
            res_model: "clover.sale",
            domain: [
                ...this._dateDomain("date"),
                ["customer_display_name", "=", row.name],
            ],
        });
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
        this.state.period = "custom";
        this.refresh();
    }

    fmt(v) {
        return new Intl.NumberFormat("en-US", {
            style: "currency", currency: "USD",
        }).format(v || 0);
    }

    fmtInt(v) {
        return new Intl.NumberFormat("en-US").format(
            Math.round(v || 0));
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
