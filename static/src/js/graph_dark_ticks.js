/** @odoo-module **/
/**
 * Force axis tick labels, axis titles, and legend text on every
 * Odoo backend graph view to render in pure black.
 *
 * Root cause (verified against Odoo 19 source at
 *   addons/web/static/src/views/graph/graph_renderer.js):
 *
 *   Odoo derives its tick + legend colors from a module-level
 *   `GRAPH_LABEL_COLOR` / `GRAPH_LEGEND_COLOR` computed ONCE at
 *   import time from the `color_scheme` cookie. If that cookie
 *   was ever set to "dark" (theme flipped, browser sync, stale
 *   session…), the resolved color is `#E4E4E4` — nearly white —
 *   and it becomes hard-coded in every graph's config even when
 *   the actual page is light. Chart.js-level plugins fight a
 *   losing battle because Odoo re-writes the colors on every
 *   render inside `getScaleOptions()` / `getLegendOptions()`.
 *
 * Fix: patch those two methods directly. We now KNOW their names
 * from the source, so a proper `@web/core/utils/patch` on
 * `GraphRenderer.prototype` will win.
 *
 * We also keep the Chart.js-level plugin from the previous
 * revision as a safety net for any other Chart.js chart that
 * doesn't route through GraphRenderer (custom modules, spreadsheet
 * charts, dashboards, etc.).
 */
import { patch } from "@web/core/utils/patch";
import { GraphRenderer } from "@web/views/graph/graph_renderer";

const DARK = "#000000";
const AXIS_LINE = "rgba(0, 0, 0, 0.15)";
const TAG = "[clover-dark-ticks]";

// ----------------------------------------------------------------
// Layer 1 — patch Odoo's GraphRenderer directly.
// getScaleOptions() returns `{ x: {...}, y: {...} }` for bar/line
// charts and `{}` for pie. Both x and y have a `ticks.color` (set
// to GRAPH_LABEL_COLOR) and an optional `title.color`.
//
// getLegendOptions() returns `{ labels: { generateLabels: fn }, ... }`
// where generateLabels(chart) produces the legend row array with
// `fontColor` on each entry. We wrap that fn to overwrite fontColor.
// ----------------------------------------------------------------
patch(GraphRenderer.prototype, {
    getScaleOptions() {
        const opts = super.getScaleOptions(...arguments);
        if (opts && typeof opts === "object") {
            for (const axisKey of ["x", "y", "r"]) {
                const axis = opts[axisKey];
                if (!axis || typeof axis !== "object") continue;
                if (axis.ticks && typeof axis.ticks === "object") {
                    axis.ticks.color = DARK;
                } else {
                    axis.ticks = { color: DARK };
                }
                if (axis.title && typeof axis.title === "object") {
                    axis.title.color = DARK;
                }
                if (axis.pointLabels
                    && typeof axis.pointLabels === "object") {
                    axis.pointLabels.color = DARK;
                }
                // Faint but visible grid — improves readability
                // of dark ticks against the plot area.
                if (axis.grid && typeof axis.grid === "object"
                    && axis.grid.color !== "transparent") {
                    axis.grid.color = AXIS_LINE;
                }
            }
        }
        return opts;
    },

    getLegendOptions() {
        const opts = super.getLegendOptions(...arguments);
        if (opts && opts.labels
            && typeof opts.labels.generateLabels === "function") {
            const originalGenerate = opts.labels.generateLabels;
            opts.labels.generateLabels = function (chart) {
                const labels = originalGenerate.call(this, chart);
                if (Array.isArray(labels)) {
                    for (const l of labels) {
                        if (l && typeof l === "object") {
                            l.fontColor = DARK;
                        }
                    }
                }
                return labels;
            };
        }
        // Chart.js also honours a top-level `color` on the labels
        // object; set it as a belt-and-braces default.
        if (opts && opts.labels) {
            opts.labels.color = DARK;
        }
        return opts;
    },
});
console.info(TAG, "GraphRenderer.getScaleOptions + getLegendOptions patched.");

// ----------------------------------------------------------------
// Layer 2 — Chart.js-level defaults + plugin for anything that
// doesn't go through Odoo's GraphRenderer (spreadsheet charts,
// custom modules, third-party dashboards). Same code as before,
// mutating options in place to avoid stripping Chart.js v4's
// proxy metadata.
// ----------------------------------------------------------------
function setDark(target, prop) {
    if (target && typeof target === "object") {
        try { target[prop] = DARK; } catch (e) { /* locked */ }
    }
}

function forceDarkOptions(options) {
    if (!options || typeof options !== "object") return;
    setDark(options, "color");
    const scales = options.scales;
    if (scales && typeof scales === "object") {
        for (const key of Object.keys(scales)) {
            const scale = scales[key];
            if (!scale || typeof scale !== "object") continue;
            if (!scale.ticks) scale.ticks = {};
            setDark(scale.ticks, "color");
            if (scale.title && typeof scale.title === "object") {
                setDark(scale.title, "color");
            }
        }
    }
    const plugins = options.plugins;
    if (plugins && plugins.legend) {
        if (!plugins.legend.labels) plugins.legend.labels = {};
        setDark(plugins.legend.labels, "color");
    }
}

function findChart() {
    if (typeof window !== "undefined"
        && window.Chart
        && typeof window.Chart.register === "function") {
        return window.Chart;
    }
    try {
        const modules = odoo && odoo.loader && odoo.loader.modules;
        if (modules && modules.entries) {
            for (const [, mod] of modules.entries()) {
                if (mod && mod.Chart
                    && typeof mod.Chart.register === "function") {
                    return mod.Chart;
                }
            }
        }
    } catch (e) { /* non-fatal */ }
    return null;
}

function bootChartLayer() {
    const Chart = findChart();
    if (!Chart) {
        bootChartLayer._attempts =
            (bootChartLayer._attempts || 0) + 1;
        if (bootChartLayer._attempts < 300) {
            setTimeout(bootChartLayer, 100);
        }
        return;
    }
    if (window.__cloverDarkTickInstalled) return;
    window.__cloverDarkTickInstalled = true;
    try {
        Chart.defaults.color = DARK;
        if (Chart.overrides) {
            for (const type of Object.keys(Chart.overrides)) {
                const ov = Chart.overrides[type];
                if (!ov || !ov.scales) continue;
                for (const skey of Object.keys(ov.scales)) {
                    const sv = ov.scales[skey];
                    if (!sv) continue;
                    if (!sv.ticks) sv.ticks = {};
                    sv.ticks.color = DARK;
                    if (sv.title) sv.title.color = DARK;
                }
            }
        }
    } catch (e) { /* non-fatal */ }
    try {
        Chart.register({
            id: "cloverDarkText",
            afterInit(chart) {
                forceDarkOptions(chart.options);
            },
            beforeUpdate(chart) {
                forceDarkOptions(
                    chart.config && chart.config.options);
                forceDarkOptions(chart.options);
            },
        });
        console.info(TAG,
            "Chart.js plugin registered (safety net).");
    } catch (e) { /* non-fatal */ }
}

bootChartLayer();
