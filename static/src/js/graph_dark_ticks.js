/** @odoo-module **/
/**
 * Force every Chart.js chart in the Odoo backend to render tick
 * labels, legend text, and axis titles in pure black.
 *
 * The earlier revisions of this patch relied on
 * `GraphRenderer.getScaleOptions()` / `getChartConfig()` — those
 * either don't exist under those names in this Odoo 19 build, or
 * Odoo overwrites the returned options with its own light-grey
 * palette AFTER our patch runs. Either way, the labels stayed
 * grey.
 *
 * This revision uses two mechanisms that ALWAYS work because they
 * run at the Chart.js layer, downstream of any Odoo option
 * mutation:
 *
 *   1. A registered Chart.js plugin whose `beforeDraw` hook mutates
 *      the chart's live `options` object (color, scale ticks,
 *      legend labels, axis titles) right before the canvas is
 *      painted. Chart.js re-reads these on every animation frame,
 *      so any Odoo-side reset gets clobbered before it's visible.
 *
 *   2. A periodic canvas sweep that walks every <canvas> in the
 *      DOM, resolves its Chart.js instance via `Chart.getChart()`,
 *      patches its options, and forces a redraw — catches charts
 *      that were rendered BEFORE this asset finished loading
 *      (fast dashboards, cached pages).
 *
 * Both mechanisms need `window.Chart` — Chart.js's UMD build sets
 * that on load, and Odoo ships the UMD build in the backend
 * assets bundle. We wait for it to appear before wiring up.
 */

const DARK = "#000000";
const AXIS_LINE = "rgba(0, 0, 0, 0.15)";

/**
 * Mutate a Chart.js `options` object in-place so all text renders
 * dark. Safe on partial/missing options — every sub-key is guarded.
 */
function forceDarkOptions(options) {
    if (!options || typeof options !== "object") return;

    options.color = DARK;
    options.borderColor = options.borderColor || AXIS_LINE;

    // Scales — bar/line/pie x + y ticks and axis titles.
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
            // Faint grid lines against the darker text.
            if (scale.grid) {
                scale.grid = {
                    ...scale.grid,
                    color: AXIS_LINE,
                };
            }
        }
    }

    // Plugins — legend, title, subtitle.
    const plugins = options.plugins;
    if (plugins && typeof plugins === "object") {
        if (plugins.legend) {
            plugins.legend.labels = {
                ...(plugins.legend.labels || {}),
                color: DARK,
            };
        }
        if (plugins.title) {
            plugins.title.color = DARK;
        }
        if (plugins.subtitle) {
            plugins.subtitle.color = DARK;
        }
    }
}

/**
 * Sweep the DOM for canvas elements, resolve their Chart.js
 * instances, apply the dark options, and force a redraw.
 */
function sweepCanvases(Chart) {
    const canvases = document.querySelectorAll("canvas");
    for (const canvas of canvases) {
        let chart = null;
        try {
            chart = Chart.getChart && Chart.getChart(canvas);
        } catch (e) {
            continue;
        }
        if (!chart || !chart.options) continue;
        forceDarkOptions(chart.options);
        try {
            chart.update("none");
        } catch (e) {
            /* transient render errors are fine — the next
               beforeDraw pass will still darken the labels. */
        }
    }
}

/**
 * Bootstraps the plugin + sweeper once window.Chart is available.
 */
function boot() {
    const Chart = typeof window !== "undefined" && window.Chart;
    if (!Chart || typeof Chart.register !== "function") {
        // Chart.js is loaded with the graph view bundle, which
        // may resolve after this asset. Retry until it's ready
        // (or give up after ~30s).
        boot._attempts = (boot._attempts || 0) + 1;
        if (boot._attempts < 300) {
            setTimeout(boot, 100);
        }
        return;
    }
    if (window.__cloverDarkTickInstalled) return;
    window.__cloverDarkTickInstalled = true;

    // -- Layer A: rewrite defaults so brand-new charts start dark.
    try {
        Chart.defaults.color = DARK;
        if (Chart.defaults.font) {
            Chart.defaults.font.weight = "500";
        }
        if (Chart.defaults.scale && Chart.defaults.scale.ticks) {
            Chart.defaults.scale.ticks.color = DARK;
        }
        if (Chart.defaults.plugins &&
            Chart.defaults.plugins.legend &&
            Chart.defaults.plugins.legend.labels) {
            Chart.defaults.plugins.legend.labels.color = DARK;
        }
    } catch (e) {
        /* Chart.defaults shape varies by version — non-fatal. */
    }

    // -- Layer B: plugin that darkens options on every draw.
    Chart.register({
        id: "cloverDarkText",
        beforeUpdate(chart) {
            forceDarkOptions(chart.config && chart.config.options);
        },
        beforeDraw(chart) {
            forceDarkOptions(chart.options);
        },
    });

    // -- Layer C: sweep already-rendered charts.
    sweepCanvases(Chart);
    // Keep sweeping — a graph view swap doesn't always fire the
    // right lifecycle for a newly-mounted chart to pick us up.
    setInterval(() => sweepCanvases(Chart), 1500);
}

boot();
