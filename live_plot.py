"""
Live plotting of Apollo AIR-1 sensor data via ESPHome SSE stream.
Continuously reads /events and updates matplotlib charts in real time.
"""

import json
import threading
import time
from collections import deque
from datetime import datetime

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import requests

DEVICE_URL = "http://192.168.1.62"
HISTORY_SECONDS = 600  # 10 minutes of history

# Sensors to track, grouped for subplots
SENSOR_GROUPS = {
    "CO2 (ppm)": ["sensor-co2"],
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

# Short display names for legend
DISPLAY_NAMES = {
    "sensor-co2": "CO2",
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

# All sensor IDs we care about
ALL_SENSORS = {sid for group in SENSOR_GROUPS.values() for sid in group}


class SensorData:
    """Thread-safe rolling buffer for sensor readings."""

    def __init__(self, max_age: int = HISTORY_SECONDS):
        self.max_age = max_age
        self.lock = threading.Lock()
        # sensor_id -> deque of (datetime, float)
        self.data: dict[str, deque[tuple[datetime, float]]] = {
            sid: deque() for sid in ALL_SENSORS
        }

    def add(self, sensor_id: str, value: float):
        if sensor_id not in self.data:
            return
        now = datetime.now()
        with self.lock:
            self.data[sensor_id].append((now, value))
            # Trim old entries
            cutoff = time.time() - self.max_age
            while (
                self.data[sensor_id] and self.data[sensor_id][0][0].timestamp() < cutoff
            ):
                self.data[sensor_id].popleft()

    def get(self, sensor_id: str) -> tuple[list[datetime], list[float]]:
        with self.lock:
            times = [t for t, _ in self.data[sensor_id]]
            values = [v for _, v in self.data[sensor_id]]
        return times, values


def sse_reader(store: SensorData, stop_event: threading.Event):
    """Background thread: reads SSE /events stream and stores values."""
    while not stop_event.is_set():
        try:
            resp = requests.get(f"{DEVICE_URL}/events", stream=True, timeout=15)
            for line in resp.iter_lines(decode_unicode=True):
                if stop_event.is_set():
                    break
                if not line or not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                    sid = data.get("id", "")
                    if sid in ALL_SENSORS:
                        val = data.get("value")
                        if val is not None and val != "NA":
                            store.add(sid, float(val))
                except json.JSONDecodeError, ValueError, TypeError:
                    pass
            resp.close()
        except Exception as e:
            print(f"SSE connection error: {e}, reconnecting in 2s...")
            time.sleep(2)


def main():
    store = SensorData()
    stop_event = threading.Event()

    # Start SSE reader in background
    reader = threading.Thread(target=sse_reader, args=(store, stop_event), daemon=True)
    reader.start()

    # Set up subplots
    n = len(SENSOR_GROUPS)
    fig, axes = plt.subplots(n, 1, figsize=(12, 2.5 * n), sharex=True)
    fig.suptitle("Apollo AIR-1 Live Data", fontsize=14)
    if n == 1:
        axes = [axes]

    plt.ion()
    plt.tight_layout(rect=[0.05, 0.03, 1, 0.96])
    plt.subplots_adjust(hspace=0.3)

    # Format x-axis
    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))

    try:
        while plt.fignum_exists(fig.number):
            for ax, (title, sensor_ids) in zip(axes, SENSOR_GROUPS.items()):
                ax.clear()
                ax.set_ylabel(title, fontsize=9)
                ax.tick_params(labelsize=8)
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
                ax.grid(True, alpha=0.3)

                for sid in sensor_ids:
                    times, values = store.get(sid)
                    if times:
                        label = DISPLAY_NAMES.get(sid, sid)
                        ax.plot(times, values, ".", label=label, linewidth=1)

                if len(sensor_ids) > 1:
                    ax.legend(loc="upper left", fontsize=7)

            fig.canvas.draw_idle()
            fig.canvas.flush_events()
            plt.pause(1)

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        plt.close("all")
        print("Stopped.")


if __name__ == "__main__":
    main()
