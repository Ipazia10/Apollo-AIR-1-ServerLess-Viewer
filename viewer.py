"""
Interactive viewer for Apollo AIR-1 sensor data stored in SQLite.
Uses Dash + Plotly with WebGL rendering and LTTB downsampling
to handle millions of data points without crashing the browser.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from dash import Dash, Input, Output, callback, dcc, html
from plotly.subplots import make_subplots

DB_PATH = str(Path(__file__).with_name("sensor_data.db"))

# Max points per trace sent to the browser.
# WebGL (scattergl) handles this fine; LTTB keeps visual fidelity.
MAX_POINTS = 4000

SENSOR_GROUPS = {
    "CO₂ (ppm)": ["sensor-co2"],
    "Temperature (°C)": ["sensor-sen55_temperature"],
    "Humidity (%)": ["sensor-sen55_humidity"],
    "Pressure (hPa)": ["sensor-dps310_pressure"],
    "VOC / NOx Index": ["sensor-sen55_voc", "sensor-sen55_nox"],
    "PM (µg/m³)": [
        "sensor-pm__1_m_weight_concentration",
        "sensor-pm__2_5_m_weight_concentration",
        "sensor-pm__4_m_weight_concentration",
        "sensor-pm__10_m_weight_concentration",
    ],
}

DISPLAY_NAMES = {
    "sensor-co2": "CO₂",
    "sensor-sen55_temperature": "Temp",
    "sensor-sen55_humidity": "Humidity",
    "sensor-dps310_pressure": "Pressure",
    "sensor-sen55_voc": "VOC",
    "sensor-sen55_nox": "NOx",
    "sensor-pm__1_m_weight_concentration": "PM1.0",
    "sensor-pm__2_5_m_weight_concentration": "PM2.5",
    "sensor-pm__4_m_weight_concentration": "PM4.0",
    "sensor-pm__10_m_weight_concentration": "PM10",
}


def lttb_downsample(
    x: np.ndarray,
    y: np.ndarray,
    n: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Largest-Triangle-Three-Buckets downsampling."""
    length = len(x)
    if length <= n:
        return x, y

    out_x = np.empty(n, dtype=x.dtype)
    out_y = np.empty(n, dtype=y.dtype)

    # Always keep first and last
    out_x[0], out_y[0] = x[0], y[0]
    out_x[n - 1], out_y[n - 1] = x[-1], y[-1]

    bucket_size = (length - 2) / (n - 2)

    for i in range(1, n - 1):
        # Next bucket boundaries
        b_start = int((i - 1) * bucket_size) + 1
        b_end = int(i * bucket_size) + 1
        # Average of the bucket after this one (for triangle area)
        c_start = int(i * bucket_size) + 1
        c_end = int((i + 1) * bucket_size) + 1
        if c_end > length - 1:
            c_end = length - 1

        avg_x = np.mean(x[c_start : c_end + 1].astype(np.float64))
        avg_y = np.mean(y[c_start : c_end + 1])

        # Pick point in current bucket with largest triangle area
        ax, ay = float(out_x[i - 1]), float(out_y[i - 1])
        best_area = -1.0
        best_idx = b_start
        for j in range(b_start, min(b_end + 1, length)):
            area = abs(
                (float(x[j]) - ax) * (avg_y - ay) - (avg_x - ax) * (float(y[j]) - ay),
            )
            if area > best_area:
                best_area = area
                best_idx = j

        out_x[i] = x[best_idx]
        out_y[i] = y[best_idx]

    return out_x, out_y


def query_sensor(
    sensor_id: str,
    t_min: float | None = None,
    t_max: float | None = None,
):
    """Fetch and downsample one sensor's data from SQLite."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    sql = "SELECT timestamp, value FROM sensor_readings WHERE sensor_id = ?"
    params: list = [sensor_id]
    if t_min is not None:
        sql += " AND timestamp >= ?"
        params.append(t_min)
    if t_max is not None:
        sql += " AND timestamp <= ?"
        params.append(t_max)
    sql += " ORDER BY timestamp"

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    if not rows:
        return np.array([]), np.array([])

    ts = np.array([r[0] for r in rows])
    vals = np.array([r[1] for r in rows])
    return lttb_downsample(ts, vals, MAX_POINTS)


def get_time_range():
    """Return (min_ts, max_ts) from the database."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    row = conn.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM sensor_readings",
    ).fetchone()
    conn.close()
    if row and row[0] is not None:
        return row[0], row[1]
    return None, None


def get_row_count():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    count = conn.execute("SELECT COUNT(*) FROM sensor_readings").fetchone()[0]
    conn.close()
    return count


# ── Dash app ──────────────────────────────────────────────────────────

app = Dash(__name__)
app.title = "Apollo AIR-1 Sensor Viewer"

app.layout = html.Div(
    style={
        "fontFamily": "system-ui, sans-serif",
        "maxWidth": "1400px",
        "margin": "0 auto",
        "padding": "20px",
    },
    children=[
        html.H2("Apollo AIR-1 Sensor Data", style={"marginBottom": "5px"}),
        html.Div(
            id="stats",
            style={"color": "#666", "marginBottom": "15px", "fontSize": "14px"},
        ),
        dcc.Graph(id="chart", style={"height": "85vh"}),
        dcc.Interval(
            id="refresh",
            interval=10_000,
            n_intervals=0,
        ),  # auto-refresh every 10s
    ],
)


@callback(
    Output("chart", "figure"),
    Output("stats", "children"),
    Input("refresh", "n_intervals"),
)
def update_chart(n_intervals):
    count = get_row_count()
    t_range_min, t_range_max = get_time_range()

    n_groups = len(SENSOR_GROUPS)
    fig = make_subplots(
        rows=n_groups,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        subplot_titles=list(SENSOR_GROUPS.keys()),
    )

    row = 1
    for title, sensor_ids in SENSOR_GROUPS.items():
        for sid in sensor_ids:
            ts, vals = query_sensor(sid)
            if len(ts) == 0:
                row += 1
                continue
            dates = [datetime.fromtimestamp(t, tz=timezone.utc).isoformat() for t in ts]
            fig.add_trace(
                go.Scattergl(
                    x=dates,
                    y=vals,
                    mode="lines",
                    name=DISPLAY_NAMES.get(sid, sid),
                    line={"width": 1},
                    legendgroup=title,
                ),
                row=row,
                col=1,
            )
        row += 1

    fig.update_layout(
        height=max(900, n_groups * 160),
        template="plotly_white",
        margin={"l": 60, "r": 20, "t": 40, "b": 40},
        legend={"orientation": "h", "y": -0.02},
        hovermode="x unified",
        uirevision="constant",  # preserve zoom/pan state across refreshes
    )

    # Enable range slider on bottom x-axis only
    fig.update_xaxes(
        rangeslider={"visible": True, "thickness": 0.04},
        row=n_groups,
        col=1,
    )

    if t_range_min and t_range_max:
        d0 = datetime.fromtimestamp(t_range_min, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M",
        )
        d1 = datetime.fromtimestamp(t_range_max, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M",
        )
        stats = f"{count:,} rows | {d0} → {d1} | Showing ≤{MAX_POINTS} pts/trace (LTTB) | Auto-refresh 10s"
    else:
        stats = f"{count:,} rows in database (no data yet)"

    return fig, stats


if __name__ == "__main__":
    print(f"Database: {DB_PATH}")
    print("Open http://127.0.0.1:8050 in your browser")
    app.run(debug=False, host="127.0.0.1", port=8050)
