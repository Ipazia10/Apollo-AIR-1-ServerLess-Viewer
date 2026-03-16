"""
Discover available sensors on an Apollo AIR-1 (ESPHome device).
Fetches the web interface and lists all sensor endpoints with current values.
"""

import json
import re

import requests

DEVICE_URL = "http://192.168.1.62"


def discover_from_events(url: str) -> list[dict]:
    """Try the SSE /events endpoint to discover all entities."""
    sensors = []
    try:
        resp = requests.get(f"{url}/events", stream=True, timeout=10)
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                    sensors.append(data)
                except json.JSONDecodeError:
                    pass
            # After collecting initial burst, break
            if len(sensors) > 50:
                break
        resp.close()
    except Exception as e:
        print(f"SSE /events failed: {e}")
    return sensors


def discover_from_html(url: str) -> list[str]:
    """Parse the main page HTML for sensor IDs."""
    try:
        resp = requests.get(url, timeout=10)
        # ESPHome web server uses IDs like "sensor-temperature", "sensor-pm_2_5"
        ids = re.findall(
            r'id="((?:sensor|binary_sensor|text_sensor|number|switch|select|button|light)-[^"]+)"',
            resp.text,
        )
        return ids
    except Exception as e:
        print(f"HTML fetch failed: {e}")
        return []


def query_sensor(url: str, entity_id: str) -> dict | None:
    """Query a single sensor REST endpoint."""
    try:
        resp = requests.get(f"{url}/{entity_id.replace('-', '/', 1)}", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def main():
    print(f"Discovering sensors on {DEVICE_URL} ...\n")

    # Method 1: Try SSE events stream
    print("=== Trying /events SSE stream ===")
    events = discover_from_events(DEVICE_URL)
    if events:
        # Group by entity type
        grouped = {}
        for e in events:
            eid = e.get("id", "unknown")
            domain = eid.split("-")[0] if "-" in eid else "other"
            grouped.setdefault(domain, []).append(e)

        for domain, items in sorted(grouped.items()):
            print(f"\n--- {domain.upper()} ---")
            for item in items:
                eid = item.get("id", "?")
                state = item.get("state", item.get("value", "?"))
                print(f"  {eid:40s}  =>  {state}")

        print(f"\nTotal entities found: {len(events)}")
        return

    # Method 2: Parse HTML
    print("SSE had no results, trying HTML parsing...")
    ids = discover_from_html(DEVICE_URL)
    if ids:
        print(f"\nFound {len(ids)} entities:\n")
        for eid in sorted(set(ids)):
            data = query_sensor(DEVICE_URL, eid)
            if data:
                state = data.get("state", data.get("value", "?"))
                print(f"  {eid:40s}  =>  {state}")
            else:
                print(f"  {eid}")
    else:
        print("No entities found. Trying raw HTML dump...")
        try:
            resp = requests.get(DEVICE_URL, timeout=10)
            print(resp.text[:3000])
        except Exception as e:
            print(f"Failed to reach device: {e}")


if __name__ == "__main__":
    main()
