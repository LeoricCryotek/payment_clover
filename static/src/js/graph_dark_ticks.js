/** @odoo-module **/
/**
 * Odoo 19's graph view (Chart.js under the hood) draws axis tick
 * labels, legend labels, and axis titles in a very light grey that
 * is hard to read on a white background — particularly the date
 * labels along the x-axis of daily-sales style graphs.
 *
 * This patch overrides GraphRenderer.getScaleOptions() so every
 * scale in every graph view renders its tick labels + axis title
 * in a near-black body colour instead. The legend colour is
 * handled the same way via getChartConfig() where available.
 *
 * Global by design: applies to every graph view in the app, not
 * just Clover reports, so charts stay visually consistent.
 */
import { patch } from "@web/core/utils/patch";
import { GraphRenderer } from "@web/views/graph/graph_renderer";

// Matches Bootstrap 5's default body foreground (also Odoo's
// $o-brand-secondary-hover) — dark enough for a11y contrast on
// #ffffff, not so dark it fights with column colours.
const DARK_TEXT = "#212529";

patch(GraphRenderer.prototype, {
    /**
     * Called by Odoo's graph view once per scale (x, y, r for pie).
     * Merges a darker `color` into ticks + axis title.
     */
    getScaleOptions() {
        const options = super.getScaleOptions(...arguments);
        if (options && typeof options === "object") {
            options.ticks = { ...(options.ticks || {}), color: DARK_TEXT };
            if (options.title) {
                options.title = { ...options.title, color: DARK_TEXT };
            }
        }
        return options;
    },

    /**
     * Called once per chart. Patches the top-level default text
     * colour + legend label colour, so pie/donut charts (which
     * don't have scales) also inherit the darker text.
     */
    getChartConfig() {
        const config = super.getChartConfig(...arguments);
        if (config && config.options) {
            config.options.color = DARK_TEXT;
            const legend = config.options.plugins &&
                           config.options.plugins.legend;
            if (legend) {
                legend.labels = { ...(legend.labels || {}),
                                  color: DARK_TEXT };
            }
        }
        return config;
    },
});
