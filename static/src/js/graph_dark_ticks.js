/** @odoo-module **/
/**
 * Darkens axis tick labels, legend text, and axis titles across
 * every Chart.js-powered graph in the Odoo backend.
 *
 * Two layers of belt-and-braces:
 *
 * 1. Patch GraphRenderer.getScaleOptions() / getChartConfig()
 *    when those methods exist (Odoo 17+ pattern). This is the
 *    "clean" hook — it runs before Chart.js is even handed the
 *    options.
 *
 * 2. Register a Chart.js `beforeInit` plugin that walks the raw
 *    Chart.js `config.options` on every chart and forces the text
 *    colour to DARK. This runs even if the GraphRenderer patch
 *    silently no-ops because Odoo changed its internal method
 *    names, and catches any third-party charts as well.
 *
 * DARK is pure black for maximum readability on white; drop it to
 * #111 or #1a1a1a if it starts to feel harsh against coloured
 * bars.
 */
import { patch } from "@web/core/utils/patch";
import { GraphRenderer } from "@web/views/graph/graph_renderer";

const DARK = "#000000";

// ---------------------------------------------------------------
// Layer 1 — patch Odoo's GraphRenderer methods when present.
// ---------------------------------------------------------------
function forceDark(options) {
    if (!options || typeof options !== "object") return options;
    options.color = DARK;
    if (options.ticks) options.ticks.color = DARK;
    if (options.title) options.title.color = DARK;
    if (options.pointLabels) options.pointLabels.color = DARK;
    return options;
}

patch(GraphRenderer.prototype, {
    getScaleOptions() {
        const options = super.getScaleOptions
            ? super.getScaleOptions(...arguments)
            : undefined;
        return forceDark(options);
    },
    getChartConfig() {
        const config = super.getChartConfig
            ? super.getChartConfig(...arguments)
            : undefined;
        if (config && config.options) {
            config.options.color = DARK;
            const scales = config.options.scales || {};
            for (const key of Object.keys(scales)) {
                forceDark(scales[key]);
            }
            const legend = config.options.plugins &&
                           config.options.plugins.legend;
            if (legend) {
                legend.labels = { ...(legend.labels || {}),
                                  color: DARK };
            }
            const tooltip = config.options.plugins &&
                            config.options.plugins.tooltip;
            if (tooltip) {
                tooltip.titleColor = "#ffffff";
                tooltip.bodyColor = "#ffffff";
            }
        }
        return config;
    },
});

// ---------------------------------------------------------------
// Layer 2 — global Chart.js plugin, in case the renderer patch
// above silently no-ops (method name drift between Odoo minor
// versions). The plugin registers itself once Chart.js is
// available on the global window.
// ---------------------------------------------------------------
function installGlobalChartPlugin() {
    const Chart = window.Chart;
    if (!Chart || typeof Chart.register !== "function") {
        // Try again shortly — Chart.js is loaded lazily with the
        // first graph view. 250ms polling is fine because this
        // never runs after the first hit.
        setTimeout(installGlobalChartPlugin, 250);
        return;
    }
    if (window.__cloverDarkTickPluginInstalled) return;
    window.__cloverDarkTickPluginInstalled = true;

    // Nuke Chart.js's own light-grey defaults.
    Chart.defaults.color = DARK;
    if (Chart.defaults.font) Chart.defaults.font.weight = "500";
    if (Chart.defaults.scale && Chart.defaults.scale.ticks) {
        Chart.defaults.scale.ticks.color = DARK;
    }
    if (Chart.defaults.plugins &&
        Chart.defaults.plugins.legend &&
        Chart.defaults.plugins.legend.labels) {
        Chart.defaults.plugins.legend.labels.color = DARK;
    }

    Chart.register({
        id: "cloverDarkTicks",
        beforeUpdate(chart) {
            const opts = chart.config && chart.config.options;
            if (!opts) return;
            opts.color = DARK;
            const scales = opts.scales || {};
            for (const key of Object.keys(scales)) {
                const scale = scales[key];
                scale.ticks = { ...(scale.ticks || {}), color: DARK };
                if (scale.title) scale.title.color = DARK;
            }
            const legend = opts.plugins && opts.plugins.legend;
            if (legend) {
                legend.labels = { ...(legend.labels || {}),
                                  color: DARK };
            }
        },
    });
}

installGlobalChartPlugin();
