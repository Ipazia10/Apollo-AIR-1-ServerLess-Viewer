"""
Log Apollo AIR-1 sensor data to SQLite via ESPHome SSE stream.
Runs continuously, writing all sensor readings to a local database.
Grafana (with the SQLite plugin) can read the DB concurrently thanks to WAL mode.
"""

import argparse
import json
import signal
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

DEVICE_URL = "http://192.168.1.62"

# All sensor IDs to log
SENSORS = {
    "sensor-co2",
    "sensor-sen55_temperature",
    "sensor-sen55_humidity",
    "sensor-dps310_pressure",
    "sensor-sen55_voc",
    "sensor-sen55_nox",
    "sensor-pm__1_m_weight_concentration",
    "sensor-pm__2_5_m_weight_concentration",
    "sensor-pm__4_m_weight_concentration",
    "sensor-pm__10_m_weight_concentration",
}

SCHEMA = """\
CREATE TABLE IF NOT EXISTS sensor_readings (
    timestamp REAL NOT NULL,
    sensor_id TEXT NOT NULL,
    value     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_time
    ON sensor_readings(timestamp);
CREATE INDEX IF NOT EXISTS idx_readings_sensor_time
    ON sensor_readings(sensor_id, timestamp);
"""

FLUSH_INTERVAL = 2  # seconds between DB flushes
STATUS_INTERVAL = 30  # seconds between status prints


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def sse_reader(
    buffer: list,
    lock: threading.Lock,
    stop: threading.Event,
):
    """Read SSE stream and append (timestamp, sensor_id, value) tuples to buffer."""
    while not stop.is_set():
        try:
            resp = requests.get(f"{DEVICE_URL}/events", stream=True, timeout=15)
            print(f"Connected to {DEVICE_URL}/events (status {resp.status_code})")
            for line in resp.iter_lines(decode_unicode=True):
                if stop.is_set():
                    break
                if not line:
                    continue
                if not line.startswith("data:"):
                    print(f"  [skip] {line[:80]}")
                    continue
                try:
                    payload = line[5:].strip()
                    data = json.loads(payload)
                    sid = data.get("id", "")
                    val = data.get("value")
                    if sid in SENSORS:
                        if val is not None and val != "NA":
                            ts = datetime.now(timezone.utc).timestamp()
                            with lock:
                                buffer.append((ts, sid, float(val)))
                            print(f"  + {sid} = {val}")
                    else:
                        print(f"  [ignored] id={sid!r}")
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    print(f"  [parse error] {exc}: {line[:80]}")
            resp.close()
        except Exception as e:
            print(f"SSE error: {e}, reconnecting in 2s...")
            stop.wait(2)


def flusher(
    conn: sqlite3.Connection,
    buffer: list,
    lock: threading.Lock,
    stop: threading.Event,
):
    """Periodically flush buffered rows to SQLite."""
    total_rows = 0
    last_status = time.monotonic()

    while not stop.is_set():
        stop.wait(FLUSH_INTERVAL)

        with lock:
            batch = list(buffer)
            buffer.clear()

        if batch:
            conn.executemany(
                "INSERT INTO sensor_readings (timestamp, sensor_id, value) VALUES (?, ?, ?)",
                batch,
            )
            conn.commit()
            total_rows += len(batch)

        now = time.monotonic()
        if now - last_status >= STATUS_INTERVAL:
            print(f"[{datetime.now():%H:%M:%S}] Total rows logged: {total_rows}")
            last_status = now

    # Final flush
    with lock:
        batch = list(buffer)
        buffer.clear()
    if batch:
        conn.executemany(
            "INSERT INTO sensor_readings (timestamp, sensor_id, value) VALUES (?, ?, ?)",
            batch,
        )
        conn.commit()
        total_rows += len(batch)
    print(f"Final flush complete. Total rows: {total_rows}")


def main():
    parser = argparse.ArgumentParser(
        description="Log Apollo AIR-1 sensor data to SQLite",
    )
    parser.add_argument(
        "--db",
        default="sensor_data.db",
        help="Path to SQLite database file (default: sensor_data.db)",
    )
    args = parser.parse_args()

    db_path = str(Path(args.db).resolve())
    conn = init_db(db_path)
    print(f"Database: {db_path}")

    buffer: list[tuple[float, str, float]] = []
    lock = threading.Lock()
    stop = threading.Event()

    # Graceful shutdown on Ctrl+C / SIGTERM
    def shutdown(sig, frame):
        print("\nShutting down...")
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    reader_thread = threading.Thread(
        target=sse_reader,
        args=(buffer, lock, stop),
        daemon=True,
    )
    flush_thread = threading.Thread(
        target=flusher,
        args=(conn, buffer, lock, stop),
        daemon=True,
    )

    reader_thread.start()
    flush_thread.start()

    # Block main thread until stop is requested
    try:
        while not stop.is_set():
            stop.wait(1)
    except KeyboardInterrupt:
        stop.set()

    reader_thread.join(timeout=5)
    flush_thread.join(timeout=5)
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
