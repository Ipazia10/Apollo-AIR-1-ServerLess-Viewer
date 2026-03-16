# Apollo AIR-1 Serverless Viewer

> **Disclaimer:** This is vibe-coded. Built with Claude, iterated until it worked. No tests, no guarantees, no apologies.

Local tooling for the [Apollo AIR-1](https://apolloautomation.com/products/air-1) air quality sensor. Connects directly to the device's SSE stream on your LAN — no cloud, no account, no server beyond what's already on the ESP32.

## What's in the box

| File | What it does |
|---|---|
| `data_logger.py` | Logs all sensor data to SQLite (`sensor_data.db`) continuously |
| `viewer.py` | Web-based interactive viewer for the database (Dash + Plotly) |


<div align="center">
  <img src="resources\LiveChart.png" width="400">
</div>

## Sensors tracked

CO2, Temperature, Humidity, Pressure, VOC Index, NOx Index, PM1.0, PM2.5, PM4.0, PM10

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate  # Windows
pip install .
```

The device is expected at `http://192.168.1.62` (hardcoded in each script — change `DEVICE_URL` if yours differs).

## Usage

### Data logging

```bash
python data_logger.py
```

Writes sensor readings to `sensor_data.db` (SQLite, WAL mode). Runs until Ctrl+C. Reconnects automatically if the device goes offline.

### Interactive viewer

```bash
python viewer.py
```

Opens at `http://127.0.0.1:8050`. Features:

- **WebGL rendering** — won't choke on millions of points
- **LTTB downsampling** — sends max 4000 points per trace to the browser while preserving the visual shape of the data
- **Zoom & pan** — drag to zoom, double-click to reset
- **Range slider** — quick time navigation at the bottom
- **Auto-refresh** — picks up new data every 10 seconds
- **Safe concurrent access** — reads the DB in read-only mode while the logger writes (WAL mode)

You can run `data_logger.py` and `viewer.py` at the same time.

## Architecture

```
Apollo AIR-1 (ESP32)
    │
    │  SSE stream (http://<device>/events)
    │
    └── data_logger.py    →  sensor_data.db (SQLite, WAL)
                                  │
                                  └── viewer.py  →  browser (localhost:8050)
```
