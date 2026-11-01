"""Server-side geometry for the small single-series column charts on dashboards and reports.

Charts are drawn as inline SVG by `_column_chart.html`, so no charting library is needed.
Each chart also carries a table view, and site.js adds hover/focus tooltips.
"""

import math

CHART_WIDTH = 520
CHART_HEIGHT = 220
PLOT_TOP = 22       # Room for value labels above the tallest column.
PLOT_BOTTOM = 28    # Room for category labels.
PLOT_LEFT = 44      # Room for y-axis ticks.
MAX_BAR_WIDTH = 24
BAR_RADIUS = 4


def nice_ceiling(value):
    """Round a maximum up to a clean axis value such as 5, 10, 25, 50 or 100."""
    if value <= 0:
        return 1
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def compact(value):
    value = float(value)
    for limit, suffix in ((1_000_000, "M"), (1_000, "K")):
        if abs(value) >= limit:
            text = f"{value / limit:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return f"{value:.0f}" if value == int(value) else f"{value:.1f}"


def column_path(x, top, width, base):
    """A column with a 4px rounded data-end and a square baseline."""
    radius = min(BAR_RADIUS, width / 2, base - top)
    if radius <= 0:
        return f"M{x:.1f},{base:.1f}H{x + width:.1f}Z"
    return (
        f"M{x:.1f},{base:.1f}V{top + radius:.1f}Q{x:.1f},{top:.1f} {x + radius:.1f},{top:.1f}"
        f"H{x + width - radius:.1f}Q{x + width:.1f},{top:.1f} {x + width:.1f},{top + radius:.1f}V{base:.1f}Z"
    )


def column_chart(points, value_prefix="", value_suffix="", max_labels=7):
    """Build a chart from [(label, value), ...]. Values are drawn on the column caps.

    At most `max_labels` category labels are drawn so they never collide; the last
    category is always labelled, and the tooltip and table carry every value.
    """
    points = [(label, float(value or 0)) for label, value in points]
    label_every = max(1, math.ceil(len(points) / max_labels))
    maximum = nice_ceiling(max((value for _, value in points), default=0))
    plot_width = CHART_WIDTH - PLOT_LEFT
    plot_height = CHART_HEIGHT - PLOT_TOP - PLOT_BOTTOM
    base = PLOT_TOP + plot_height
    slot = plot_width / max(len(points), 1)
    bar_width = min(MAX_BAR_WIDTH, slot * 0.6)
    columns = []
    for index, (label, value) in enumerate(points):
        height = plot_height * value / maximum
        x = PLOT_LEFT + slot * index + (slot - bar_width) / 2
        top = base - height
        display = f"{value_prefix}{value:,.0f}{value_suffix}"
        columns.append({
            "label": label, "value": value, "display": display,
            "path": column_path(x, top, bar_width, base) if value > 0 else "",
            "center": x + bar_width / 2, "top": top,
            "hit_x": PLOT_LEFT + slot * index, "hit_width": slot,
            "show_label": index == len(points) - 1 or (index % label_every == 0 and len(points) - 1 - index >= label_every),
            # Label only the latest and the largest value; the tooltip and table carry the rest.
            "show_value": False,
        })
    if columns:
        columns[-1]["show_value"] = True
        peak = max(columns, key=lambda column: column["value"])
        if peak["value"] > 0:
            peak["show_value"] = True
    ticks = [{"y": base - plot_height * step / 4, "label": f"{value_prefix}{compact(maximum * step / 4)}"} for step in range(5)]
    return {
        "width": CHART_WIDTH, "height": CHART_HEIGHT, "base": base, "left": PLOT_LEFT,
        "columns": columns, "ticks": ticks, "has_data": any(column["value"] > 0 for column in columns),
    }
