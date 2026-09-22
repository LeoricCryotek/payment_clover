/** @odoo-module **/
/**
 * Darkens axis tick labels, legend text, and axis titles for every
 * Chart.js chart in the Odoo backend.
 *
 * Prior revisions relied on `window.Chart` — Odoo 19 uses ES
 * modules and does NOT set that global, so those revisions ran
 * successfully but their patches never touched a real Chart
 * instance. This revision:
 *
 *   * Locates Chart.js via three fallbacks:
 *       (a) window.Chart (UMD builds still expose it),
 *       (b) Odoo's module loader — scans every loaded module for
 *           an export whose shape matches Chart's static API,
 *       (c) DOM sniff — grabs a Chart instance off any existing
 *           <canvas> via `Chart.getChart` and reads its constructor.
 *
 *   * Applies dark text at THREE levels of Chart.js config:
 *       (i)   `Chart.defaults.color` and per-scale-type defaults
 *             (Chart.js v4 stores linear/category/time defaults
 *             separately under `Chart.defaults.scales.<type>`),
 *       (ii)  a plugin whose `beforeUpdate` + `beforeDraw` hooks
 *             re-apply the dark color on every render frame,
 *       (iii) an interval sweep that walks every canvas in the
 *             DOM, patches its options, and forces an update.
 *
 *   * Logs progress to the browser console under the tag
 *     `[clover-dark-ticks]` so we can diagnose remotely — check
 *     the console after a hard-refresh to see which layer found
 *     Chart and how many charts were patched.
 */

const DARK = "#000000";
const AXIS_LINE = "rgba(0, 0, 0, 0.15)";
const TAG = "[clover-dark-ticks]";

// ---- Chart discovery ------------------------------------------

function chartLooksReal(obj) {
    return obj && typeof obj === "object"
        && typeof obj.register === "function"
        && obj.defaults
        && typeof obj.getChart === "function";
}

function findChartViaWindow() {
    if (typeof window !== "undefined" && chartLooksReal(window.Chart)) {
        return window.Chart;
    }
    return null;
}

function findChartViaLoader() {
    try {
        const modules = odoo && odoo.loader && odoo.loader.modules;
        if (!modules || !modules.entries) return null;
        for (const [, mod] of modules.entries()) {
            if (!mod) continue;
            if (chartLooksReal(mod.Chart)) return mod.Chart;
            if (chartLooksReal(mod.default)) return mod.default;
        }
    } catch (e) {
        // Non-fatal
    }
    return null;
}

function findChartViaCanvas() {
    // Any existing chart on the page exposes its constructor.
    const candidate = window.Chart || null;
    if (chartLooksReal(candidate)) return candidate;
    // Try to grab a Chart instance and get its constructor.
    const canvases = document.querySelectorAll("canvas");
    for (const canvas of canvases) {
        try {
            // Chart.js stores instances on _chartjs-like data
            // attributes; direct access differs by version, so
            // just try Chart.getChart via any Chart we might find.
            const c = window.Chart && window.Chart.getChart
                ? window.Chart.getChart(canvas)
                : null;
            if (c && c.constructor) {
                return c.constructor;
            }
        } catch (e) {
            // Skip
        }
    }
    return null;
}

function findChart() {
    return findChartViaWindow()
        || findChartViaLoader()
        || findChartViaCanvas();
}

// ---- Option mutation ------------------------------------------

function forceDarkOptions(options) {
    if (!options || typeof options !== "object") return;
    options.color = DARK;

    const scales = options.scales;
    if (scales && typeof scales === "object") {
        for (const key of Object.keys(scales)) {
            const scale = scales[key];
            if (!scale || typeof scale !== "object") continue;
            scale.ticks = { ...(scale.ticks || {}), color: DARK };
            if (scale.title) {
                scale.title = { ...scale.title, color: DARK };
            }
            if (scale.pointLabels) {
                scale.pointLabels = {
                    ...scale.pointLabels, color: DARK,
                };
            }
            if (scale.grid) {
                scale.grid = {
                    ...scale.grid, color: AXIS_LINE,
                };
            }
        }
    }

    const plugins = options.plugins;
    if (plugins && typeof plugins === "object") {
        if (plugins.legend) {
            plugins.legend.labels = {
                ...(plugins.legend.labels || {}),
                color: DARK,
            };
        }
        if (plugins.title) plugins.title.color = DARK;
        if (plugins.subtitle) plugins.subtitle.color = DARK;
    }
}

function forceDarkDefaults(Chart) {
    try {
        Chart.defaults.color = DARK;
        Chart.defaults.borderColor = AXIS_LINE;
        if (Chart.defaults.font) {
            Chart.defaults.font.weight = "500";
        }
    } catch (e) { /* non-fatal */ }

    // Per-scale-type defaults — Chart.js v4 stores these under
    // Chart.defaults.scales.<type> and each chart inherits from
    // its scale type BEFORE Chart.defaults.color is consulted, so
    // setting only Chart.defaults.color is insufficient.
    try {
        const scales = Chart.defaults.scales;
        if (scales) {
            for (const type of Object.keys(scales)) {
                const sd = scales[type];
                if (!sd) continue;
                sd.ticks = sd.ticks || {};
                sd.ticks.color = DARK;
                sd.title = sd.title || {};
                sd.title.color = DARK;
                if (sd.grid) sd.grid.color = AXIS_LINE;
            }
        }
    } catch (e) { /* non-fatal */ }

    try {
        const legend = Chart.defaults.plugins
            && Chart.defaults.plugins.legend;
        if (legend && legend.labels) {
            legend.labels.color = DARK;
        }
    } catch (e) { /* non-fatal */ }
}

function sweepCanvases(Chart) {
    if (!Chart.getChart) return 0;
    let touched = 0;
    document.querySelectorAll("canvas").forEach((canvas) => {
        try {
            const inst = Chart.getChart(canvas);
            if (!inst || !inst.options) return;
            forceDarkOptions(inst.options);
            inst.update("none");
            touched++;
        } catch (e) {
            /* transient */
        }
    });
    return touched;
}

// ---- Boot -----------------------------------------------------

function boot() {
    const Chart = findChart();
    if (!Chart) {
        boot._attempts = (boot._attempts || 0) + 1;
        if (boot._attempts < 300) {
            setTimeout(boot, 100);
        } else {
            console.warn(TAG,
                "Chart.js not found after 30s of polling. Graph "
                + "labels will remain at Odoo defaults. Open a "
                + "graph view (e.g. Clover → Dashboard → Overview) "
                + "and reload — sometimes Chart.js is only loaded "
                + "lazily on first graph render.");
        }
        return;
    }
    if (window.__cloverDarkTickInstalled) return;
    window.__cloverDarkTickInstalled = true;
    console.info(TAG, "Chart.js found via",
        window.Chart === Chart ? "window.Chart"
        : "loader / canvas sniff.");

    forceDarkDefaults(Chart);
    console.info(TAG, "Defaults set: Chart.defaults.color =",
                 Chart.defaults.color);

    try {
        Chart.register({
            id: "cloverDarkText",
            beforeUpdate(chart) {
                forceDarkOptions(chart.config && chart.config.options);
            },
            beforeDraw(chart) {
                forceDarkOptions(chart.options);
            },
        });
        console.info(TAG, "Plugin registered.");
    } catch (e) {
        console.warn(TAG, "Plugin registration failed:", e);
    }

    setTimeout(() => {
        const n = sweepCanvases(Chart);
        console.info(TAG, "Initial canvas sweep patched", n, "chart(s).");
    }, 500);

    // Keep sweeping — new dashboards can render charts after
    // initial mount without firing a global lifecycle we hooked.
    setInterval(() => sweepCanvases(Chart), 1500);
}

boot();
