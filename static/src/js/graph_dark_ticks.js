/** @odoo-module **/
/**
 * Darkens axis tick labels, legend text, and axis titles for every
 * Chart.js chart in the Odoo backend.
 *
 * IMPORTANT: Chart.js v4 wraps option objects with a proxy that
 * carries internal state ($context, _indexable flags, scriptable
 * resolvers, etc.). REPLACING an option object with a plain spread
 * copy strips that metadata and causes Chart.js's `_scriptable` to
 * crash on the next redraw with:
 *
 *   TypeError: name.startsWith is not a function.
 *
 * This revision mutates the existing option objects in place
 * (assigning individual properties) rather than replacing them,
 * and moves the heavy work out of `beforeDraw` (fires every
 * animation frame) into `beforeUpdate` / `afterInit` (fire once
 * per data change / creation).
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

function findChart() {
    if (typeof window !== "undefined" && chartLooksReal(window.Chart)) {
        return window.Chart;
    }
    try {
        const modules = odoo && odoo.loader && odoo.loader.modules;
        if (modules && modules.entries) {
            for (const [, mod] of modules.entries()) {
                if (!mod) continue;
                if (chartLooksReal(mod.Chart)) return mod.Chart;
                if (chartLooksReal(mod.default)) return mod.default;
            }
        }
    } catch (e) { /* non-fatal */ }
    try {
        const canvases = document.querySelectorAll("canvas");
        for (const canvas of canvases) {
            const c = window.Chart && window.Chart.getChart
                ? window.Chart.getChart(canvas)
                : null;
            if (c && c.constructor && chartLooksReal(c.constructor)) {
                return c.constructor;
            }
        }
    } catch (e) { /* non-fatal */ }
    return null;
}

// ---- Option mutation (IN-PLACE — never replace objects) --------

function setDark(target, prop) {
    if (target && typeof target === "object") {
        try {
            target[prop] = DARK;
        } catch (e) { /* Chart.js may lock some option props */ }
    }
}

function setLine(target, prop) {
    if (target && typeof target === "object") {
        try {
            target[prop] = AXIS_LINE;
        } catch (e) { /* non-fatal */ }
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
            // Mutate existing sub-objects in place. If a sub-object
            // is missing, create an empty plain one only once — do
            // NOT re-create on every call.
            if (!scale.ticks) scale.ticks = {};
            setDark(scale.ticks, "color");
            if (scale.title && typeof scale.title === "object") {
                setDark(scale.title, "color");
            }
            if (scale.pointLabels
                && typeof scale.pointLabels === "object") {
                setDark(scale.pointLabels, "color");
            }
            if (scale.grid && typeof scale.grid === "object") {
                setLine(scale.grid, "color");
            }
        }
    }

    const plugins = options.plugins;
    if (plugins && typeof plugins === "object") {
        if (plugins.legend && typeof plugins.legend === "object") {
            if (!plugins.legend.labels) plugins.legend.labels = {};
            setDark(plugins.legend.labels, "color");
        }
        if (plugins.title && typeof plugins.title === "object") {
            setDark(plugins.title, "color");
        }
        if (plugins.subtitle
            && typeof plugins.subtitle === "object") {
            setDark(plugins.subtitle, "color");
        }
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

    try {
        const scales = Chart.defaults.scales;
        if (scales) {
            for (const type of Object.keys(scales)) {
                const sd = scales[type];
                if (!sd) continue;
                if (!sd.ticks) sd.ticks = {};
                sd.ticks.color = DARK;
                if (!sd.title) sd.title = {};
                sd.title.color = DARK;
                if (sd.grid) sd.grid.color = AXIS_LINE;
            }
        }
    } catch (e) { /* non-fatal */ }

    try {
        const legend = Chart.defaults.plugins
            && Chart.defaults.plugins.legend;
        if (legend) {
            if (!legend.labels) legend.labels = {};
            legend.labels.color = DARK;
        }
    } catch (e) { /* non-fatal */ }
}

// ---- Boot -----------------------------------------------------

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
        } catch (e) { /* transient */ }
    });
    return touched;
}

function boot() {
    const Chart = findChart();
    if (!Chart) {
        boot._attempts = (boot._attempts || 0) + 1;
        if (boot._attempts < 300) {
            setTimeout(boot, 100);
        } else {
            console.warn(TAG,
                "Chart.js not found after 30s of polling.");
        }
        return;
    }
    if (window.__cloverDarkTickInstalled) return;
    window.__cloverDarkTickInstalled = true;
    console.info(TAG, "Chart.js located; installing dark-text hooks.");

    forceDarkDefaults(Chart);

    try {
        Chart.register({
            id: "cloverDarkText",
            // Runs once per chart create — set colors before the
            // first paint.
            afterInit(chart) {
                forceDarkOptions(chart.options);
            },
            // Runs whenever the data/config changes — re-apply so
            // Odoo can't reset our colors.
            beforeUpdate(chart) {
                forceDarkOptions(
                    chart.config && chart.config.options);
                forceDarkOptions(chart.options);
            },
        });
        console.info(TAG, "Plugin registered.");
    } catch (e) {
        console.warn(TAG, "Plugin registration failed:", e);
    }

    setTimeout(() => {
        const n = sweepCanvases(Chart);
        console.info(TAG, "Initial canvas sweep patched",
                     n, "chart(s).");
    }, 500);

    // Slower sweep — the plugin handles most cases; this is only
    // a safety net for late-mounted charts.
    setInterval(() => sweepCanvases(Chart), 3000);
}

boot();
