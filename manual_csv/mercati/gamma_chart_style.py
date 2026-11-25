"""Visual-only SPX chart template for Nasdaq NDX, Gold GLD and Petrolio USO.

Graph geometry/labels are ported from:
GrespoSan/Serafini-SPX-Dashboard/gamma_section.py lines 849–948.
Original market Gamma values, strike choices, basis and ETF/future mapping
are never modified by this module.
"""
from datetime import date, datetime
import math

import plotly.graph_objects as go

SPX_GREEN = "#2dbb72"
SPX_RED = "#e45757"
SPX_ZERO = "#b7bec8"
SPX_CLOSE = "#f0cc65"
SPX_GUIDE = "rgba(195,205,220,0.46)"
SPX_GUIDE_BOLD = "rgba(205,215,230,0.62)"
SPX_BG = "#0e1017"

# We preserve a shared Gamma X-axis between the two option maturities.
# SPX source uses min/max over both panels plus 8% of the full spread.
def spx_shared_gamma_bounds(groups, field="net", cap=None):
    values = [float(row[field]) for p in groups for row in p.get("rows", [])]
    if not all(math.isfinite(x) for x in values):
        raise ValueError("NET GAMMA non finiti.")
    low, high = min([0.0] + values), max([0.0] + values)
    margin = max(high - low, 1.0) * .08
    return [low - margin, high + margin]


def top_levels(rows, level_field, value_field, shift=0.0):
    """Optional raw strikes for downstream reporting; never changes the chart."""
    eligible = []
    for row in rows:
        try:
            level = float(row[level_field]) + shift
            value = float(row[value_field])
        except (TypeError, ValueError, KeyError):
            continue
        if math.isfinite(level) and math.isfinite(value):
            eligible.append((level, value))
    if not eligible:
        return {"positive": None, "negative": None, "total": None}
    pos = [r for r in eligible if r[1] > 0]
    neg = [r for r in eligible if r[1] < 0]
    return {
        "positive": max(pos, key=lambda r: r[1]) if pos else None,
        "negative": min(neg, key=lambda r: r[1]) if neg else None,
        "total": sum(x[1] for x in eligible),
    }


def capture_display(source_timestamp=None, downloaded_rome=None, source=""):
    """Date/clock are ONLY sourced from existing documented metadata.

    Original Cboe timestamps have no confirmed time zone and are not
    transformed to Rome, Chicago, or New York.
    """
    day = None
    if source_timestamp:
        try:
            day = date.fromisoformat(str(source_timestamp).strip()[:10])
        except (ValueError, TypeError):
            pass
    clock = None
    if downloaded_rome:
        try:
            raw = str(downloaded_rome)
            # Either ISO 2026-10-29T21:56:13+01:00, or DD/MM/YYYY HH:MM:SS Roma.
            if "T" in raw:
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is not None:
                    from zoneinfo import ZoneInfo
                    parsed = parsed.astimezone(ZoneInfo("Europe/Rome"))
                clock = parsed.strftime("%H:%M")
            elif len(raw) >= 16 and raw[2] == "/":
                clock = raw[11:16]
        except (ValueError, TypeError):
            pass
    if source == "CSV Cboe" and clock:
        return (day.strftime("%d/%m/%Y") if day else "Data non disponibile", f"CSV · ore {clock} Roma")
    if "Archivio" in str(source):
        label = "Archivio"
    elif clock:
        label = "Acquisizione manuale"
    else:
        label = "File originale"
    download = f"download ore {clock} Roma" if clock else "ora download non disponibile"
    day_text = day.strftime("%d/%m/%Y") if day else "data non verificata"
    return day_text, f"{label} · {download}"


def panel_heading(kind, expiry, source_timestamp=None, downloaded_rome=None, source=""):
    day_text, clock_info = capture_display(source_timestamp, downloaded_rome, source)
    title = f"{kind} — RILEVAMENTO {day_text} · {clock_info}"
    info = (f"Scadenza opzioni: {expiry} · Timestamp originale Cboe: "
            f"{source_timestamp or 'non disponibile'} (fuso non dichiarato)")
    if source == "CSV Cboe":
        info = f"Scadenza opzioni: {expiry} · Fotografia Cboe: {source_timestamp} UTC"
    return title, info


def profile_chart(rows, symbol, reference, common_x, shared_y, *,
                  level_field="strike", gamma_field="net",
                  price_shift=0.0, price_scale=1.0, axis_symbol=None,
                  ndx=False, show_close=True, close_label="Close prec.",
                  title="0DTE", snapshot_label="", tick_levels=None):
    """Exact SPX color/axes/title/guides, with thin bars on sparse ETF strikes.

    Preserve the numeric X and Y axes passed in; style is never a Gamma
    transformation. The bar thickness is capped to about 7 vertical pixels,
    to avoid 10 ETF strikes becoming huge rectangles in a 760px plot.
    """
    if not rows:
        raise ValueError("Profilo senza strike.")
    if not (math.isfinite(float(price_scale)) and float(price_scale) > 0
            and math.isfinite(float(price_shift))):
        raise ValueError("Conversione di prezzo futuro non valida.")
    levels = [float(r[level_field]) * price_scale + price_shift for r in rows]
    values = [float(r[gamma_field]) for r in rows]
    if not all(math.isfinite(x) for x in levels + values):
        raise ValueError("Gamma/strike non finito.")

    plot_height = max(520, 760)
    visible_height = max(1.0, float(shared_y[1]) - float(shared_y[0]))
    gaps = [b-a for a,b in zip(sorted(set(levels)),sorted(set(levels))[1:]) if b>a]
    min_step = min(gaps) if gaps else visible_height
    # Same thin SPX appearance whether the market has 10 or 50 strikes.
    bar_height = min(min_step * .72, visible_height * 7.0 / (plot_height - 135))
    bar_height = max(bar_height, visible_height / 10000.0)

    custom = []
    for r in rows:
        if ndx:
            custom.append([r["strike_index"], r["call_gamma"], r["put_gamma"]])
        else:
            custom.append([r["strike"], r["call"], r["put"],
                           r["call_gamma"], r["put_gamma"], r["call_oi"],
                           r["put_oi"], r["call_volume"], r["put_volume"]])
    template = (
        "Strike %{customdata[0]:,.2f}<br>NET GAMMA: %{x:,.3f}"
        "<br>Componente Call (+): %{customdata[1]:,.3f}"
        "<br>Componente Put (−): %{customdata[2]:,.3f}<extra></extra>"
    ) if ndx else (
        "Strike %{customdata[0]:,.2f}<br>NET GAMMA: %{x:,.3f}"
        "<br>Componente Call (+): %{customdata[1]:,.3f}"
        "<br>Componente Put (−): %{customdata[2]:,.3f}"
        "<br>Gamma Call: %{customdata[3]:.6f} · Put: %{customdata[4]:.6f}"
        "<br>OI Call: %{customdata[5]:,.0f} · Put: %{customdata[6]:,.0f}"
        "<br>Volume Call: %{customdata[7]:,.0f} · Put: %{customdata[8]:,.0f}"
        "<extra></extra>"
    )
    if axis_symbol:
        template = (f"Livello {axis_symbol}: %{{y:,.2f}}<br>"
                    f"Strike {symbol} originale: %{{customdata[0]:,.2f}}<br>"
                    + template.split("<br>", 1)[1])
    fig = go.Figure()
    fig.add_bar(
        x=values, y=levels, orientation="h", name="NET GAMMA",
        width=bar_height,
        marker_color=[SPX_GREEN if v >= 0 else SPX_RED for v in values],
        customdata=custom, hovertemplate=template,
    )

    nice_title = title
    capture = snapshot_label or "fotografia Cboe originale"
    fig.update_layout(
        height=plot_height,
        autosize=True,
        margin=dict(l=15, r=20, t=55, b=32),
        title=dict(
            text=f"{nice_title} | {capture}<br>"
                 "<sup>NET GAMMA per strike — positivo a destra / negativo a sinistra</sup>",
            font=dict(size=12, color="#e5e7eb"),
            x=0.0, xanchor="left",
        ),
        xaxis=dict(
            title="NET GAMMA (unità della formula Serafini)",
            range=common_x, zeroline=True, zerolinewidth=1,
            zerolinecolor=SPX_ZERO, showgrid=False,
            fixedrange=True,
        ),
        yaxis=dict(
            title=f"Strike {axis_symbol}" if axis_symbol else (
                  "Strike NQ" if ndx and price_shift else
                  "Strike NDX" if ndx else f"Strike {symbol}"),
            range=shared_y, autorange=False, type="linear",
            tickmode="array",
            tickvals=(tick_levels if tick_levels else sorted(set(levels))),
            ticktext=[f"{v:g}" for v in (tick_levels if tick_levels else sorted(set(levels)))],
            showgrid=False,
        ),
        showlegend=False,
        template="plotly_dark",
        paper_bgcolor=SPX_BG,
        plot_bgcolor=SPX_BG,
        bargap=0.1,
        hovermode="closest",
    )
    fig.add_vline(x=0, line_width=1, line_color=SPX_ZERO)
    if show_close and math.isfinite(float(reference)):
        close = float(reference) * price_scale + price_shift
        fig.add_hline(y=close, line_dash="dash", line_width=1.5,
                      line_color=SPX_CLOSE)
        for pct in (-1.5, -1.0, -0.5, 0.5, 1.0, 1.5):
            target = close * (1 + pct / 100.0)
            # A guide outside the currently selected strike band must not
            # force Plotly's Y-axis expansion or obscure the chosen range.
            if not float(shared_y[0]) <= target <= float(shared_y[1]):
                continue
            emphasis = abs(pct) == 1.0
            fig.add_hline(
                y=target, line_dash="dot", line_width=1.8 if emphasis else 1.2,
                line_color=SPX_GUIDE_BOLD if emphasis else SPX_GUIDE,
            )
            fig.add_annotation(
                x=0.995, xref="paper", y=target, yref="y",
                text=f"{pct:+.1f}%".replace(".", ","),
                showarrow=False, xanchor="right", yanchor="middle",
                font=dict(color="rgba(225,232,240,0.92)" if emphasis
                          else "rgba(210,218,228,0.82)", size=10),
                bgcolor="rgba(13,17,23,0.78)", borderpad=1,
            )
        fig.add_annotation(
            x=0.995, xref="paper", y=close, yref="y",
            text=close_label, showarrow=False,
            xanchor="right", yanchor="bottom",
            font=dict(color=SPX_CLOSE, size=11),
            bgcolor="rgba(13,17,23,0.85)", borderpad=2,
        )
    return fig
