"""GPON terminal scraper - collects metrics from LeoX ONT and stores/exports them."""

import argparse
import json
import logging
import re
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

BASE_URL = "http://192.168.100.1"
AUTH = ("leox", "leolabs_7")
TIMEOUT = 10

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "leoxgpon.db"
JSON_PATH = DATA_DIR / "metrics.json"
PROM_PATH = DATA_DIR / "metrics.prom"
ZABBIX_PATH = DATA_DIR / "zabbix.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("leoxgpon")


def fetch(path: str) -> BeautifulSoup | None:
    """Fetch a page and return parsed BeautifulSoup or None on error."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    try:
        resp = requests.get(url, auth=AUTH, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as exc:
        log.error("fetch %s failed: %s", path, exc)
        return None


def _text(soup: BeautifulSoup, label: str) -> str:
    """Find table row by header text, return adjacent cell text."""
    for th in soup.find_all("th"):
        if label.lower() in th.get_text(strip=True).lower():
            td = th.find_next_sibling("td")
            if td:
                return td.get_text(strip=True)
    return ""


def _float(val: str) -> float | None:
    """Extract first float from string."""
    m = re.search(r"[-+]?\d+\.?\d*", val)
    return float(m.group()) if m else None


def _int(val: str) -> int | None:
    """Extract first integer from string."""
    m = re.search(r"\d+", val)
    return int(m.group()) if m else None


def scrape_status() -> dict[str, Any]:
    """Scrape device status: uptime, CPU, memory, LAN, WAN info."""
    soup = fetch("status.asp")
    if not soup:
        return {}
    result: dict[str, Any] = {
        "device_name": _text(soup, "Device Name"),
        "uptime_raw": _text(soup, "Uptime"),
        "firmware_version": _text(soup, "Firmware Version"),
        "cpu_usage_pct": _int(_text(soup, "CPU Usage")),
        "memory_usage_pct": _int(_text(soup, "Memory Usage")),
        "lan_ip": _text(soup, "IP Address"),
        "lan_subnet": _text(soup, "Subnet Mask"),
        "lan_mac": _text(soup, "MAC Address"),
        "name_servers": _text(soup, "Name Servers"),
        "ipv4_default_gw": _text(soup, "IPv4 Default Gateway"),
        "ipv6_default_gw": _text(soup, "IPv6 Default Gateway"),
    }
    # WAN table: Interface | VLAN ID | Connection Type | Protocol | IP Address | Gateway | Status
    for row in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) >= 7 and cells[0] and cells[0] not in ("LAN",):
            result["wan_interface"] = cells[0]
            result["wan_vlan_id"] = _int(cells[1])
            result["wan_conn_type"] = cells[2]
            result["wan_protocol"] = cells[3]
            result["wan_ip"] = cells[4]
            result["wan_gateway"] = cells[5]
            result["wan_status"] = cells[6]
            break
    return result


def _uptime_seconds(raw: str) -> int | None:
    """Convert uptime string like '28 days,  5:00' to seconds."""
    if not raw:
        return None
    days = 0
    hours = 0
    minutes = 0
    m = re.search(r"(\d+)\s*day", raw)
    if m:
        days = int(m.group(1))
    m = re.search(r"(\d+):(\d+)", raw)
    if m:
        hours = int(m.group(1))
        minutes = int(m.group(2))
    return days * 86400 + hours * 3600 + minutes * 60


def scrape_pon_status() -> dict[str, Any]:
    """Scrape PON optical and GPON registration status."""
    soup = fetch("status_pon.asp")
    if not soup:
        return {}
    temp_raw = _text(soup, "Temperature")
    volt_raw = _text(soup, "Voltage")
    tx_raw = _text(soup, "Tx Power")
    rx_raw = _text(soup, "Rx Power")
    bias_raw = _text(soup, "Bias Current")
    return {
        "pon_vendor": _text(soup, "Vendor Name"),
        "pon_part_number": _text(soup, "Part Number"),
        "temperature_c": _float(temp_raw),
        "voltage_v": _float(volt_raw),
        "tx_power_dbm": _float(tx_raw),
        "rx_power_dbm": _float(rx_raw),
        "bias_current_ma": _float(bias_raw),
        "onu_state": _text(soup, "ONU State"),
        "onu_id": _text(soup, "ONU ID"),
        "loid_status": _text(soup, "LOID Status"),
    }


def scrape_pon_stats() -> dict[str, Any]:
    """Scrape PON byte/packet counters."""
    soup = fetch("admin/pon-stats.asp")
    if not soup:
        return {}
    return {
        "pon_bytes_sent": _int(_text(soup, "Bytes Sent")),
        "pon_bytes_received": _int(_text(soup, "Bytes Received")),
        "pon_packets_sent": _int(_text(soup, "Packets Sent")),
        "pon_packets_received": _int(_text(soup, "Packets Received")),
        "pon_unicast_sent": _int(_text(soup, "Unicast Packets Sent")),
        "pon_unicast_received": _int(_text(soup, "Unicast Packets Received")),
        "pon_multicast_sent": _int(_text(soup, "Multicast Packets Sent")),
        "pon_multicast_received": _int(_text(soup, "Multicast Packets Received")),
        "pon_broadcast_sent": _int(_text(soup, "Broadcast Packets Sent")),
        "pon_broadcast_received": _int(_text(soup, "Broadcast Packets Received")),
        "pon_fec_errors": _int(_text(soup, "FEC Errors")),
        "pon_hec_errors": _int(_text(soup, "HEC Errors")),
        "pon_packets_dropped": _int(_text(soup, "Packets Dropped")),
        "pon_pause_sent": _int(_text(soup, "Pause Packets Sent")),
        "pon_pause_received": _int(_text(soup, "Pause Packets Received")),
    }


def scrape_interface_stats() -> dict[str, Any]:
    """Scrape LAN interface packet statistics."""
    soup = fetch("stats.asp")
    if not soup:
        return {}
    rows = soup.find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if cells and cells[0].get_text(strip=True).upper() == "LAN":
            vals = [c.get_text(strip=True) for c in cells]
            if len(vals) >= 7:
                return {
                    "lan_rx_pkt": _int(vals[1]),
                    "lan_rx_err": _int(vals[2]),
                    "lan_rx_drop": _int(vals[3]),
                    "lan_tx_pkt": _int(vals[4]),
                    "lan_tx_err": _int(vals[5]),
                    "lan_tx_drop": _int(vals[6]),
                }
    return {}


def scrape_gpon() -> dict[str, Any]:
    """Scrape GPON config: serial number, LOID."""
    soup = fetch("gpon.asp")
    if not soup:
        return {}
    serial = ""
    for th in soup.find_all("th"):
        if "serial" in th.get_text(strip=True).lower():
            td = th.find_next_sibling("td")
            if td:
                serial = td.get_text(strip=True)
    loid_input = soup.find("input", {"name": "fmgpon_loid"})
    loid = loid_input["value"] if loid_input else ""
    return {
        "gpon_serial": serial,
        "gpon_loid": loid,
    }


def scrape_arp() -> list[dict[str, str]]:
    """Scrape ARP table entries."""
    soup = fetch("arptable.asp")
    if not soup:
        return []
    entries = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            ip = cells[0].get_text(strip=True)
            mac = cells[1].get_text(strip=True)
            if ip and mac:
                entries.append({"ip": ip, "mac": mac})
    return entries


def collect_all() -> dict[str, Any]:
    """Collect all metrics and return unified dict."""
    ts = datetime.now(timezone.utc).isoformat()
    metrics: dict[str, Any] = {"timestamp": ts}
    metrics.update(scrape_status())
    metrics.update(scrape_pon_status())
    metrics.update(scrape_pon_stats())
    metrics.update(scrape_interface_stats())
    metrics.update(scrape_gpon())
    uptime_raw = metrics.get("uptime_raw", "")
    metrics["uptime_seconds"] = _uptime_seconds(uptime_raw)
    metrics["arp_table"] = scrape_arp()
    return metrics


# --- SQLite ---

_DDL = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    device_name TEXT,
    uptime_raw TEXT,
    uptime_seconds INTEGER,
    firmware_version TEXT,
    cpu_usage_pct INTEGER,
    memory_usage_pct INTEGER,
    lan_ip TEXT,
    lan_subnet TEXT,
    lan_mac TEXT,
    pon_vendor TEXT,
    pon_part_number TEXT,
    temperature_c REAL,
    voltage_v REAL,
    tx_power_dbm REAL,
    rx_power_dbm REAL,
    bias_current_ma REAL,
    onu_state TEXT,
    onu_id TEXT,
    loid_status TEXT,
    pon_bytes_sent INTEGER,
    pon_bytes_received INTEGER,
    pon_packets_sent INTEGER,
    pon_packets_received INTEGER,
    pon_unicast_sent INTEGER,
    pon_unicast_received INTEGER,
    pon_multicast_sent INTEGER,
    pon_multicast_received INTEGER,
    pon_broadcast_sent INTEGER,
    pon_broadcast_received INTEGER,
    pon_fec_errors INTEGER,
    pon_hec_errors INTEGER,
    pon_packets_dropped INTEGER,
    pon_pause_sent INTEGER,
    pon_pause_received INTEGER,
    lan_rx_pkt INTEGER,
    lan_rx_err INTEGER,
    lan_rx_drop INTEGER,
    lan_tx_pkt INTEGER,
    lan_tx_err INTEGER,
    lan_tx_drop INTEGER,
    gpon_serial TEXT,
    gpon_loid TEXT,
    name_servers TEXT,
    ipv4_default_gw TEXT,
    ipv6_default_gw TEXT,
    wan_interface TEXT,
    wan_vlan_id INTEGER,
    wan_conn_type TEXT,
    wan_protocol TEXT,
    wan_ip TEXT,
    wan_gateway TEXT,
    wan_status TEXT
);

CREATE TABLE IF NOT EXISTS arp_table (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metrics_id INTEGER NOT NULL REFERENCES metrics(id) ON DELETE CASCADE,
    ip TEXT NOT NULL,
    mac TEXT NOT NULL
);
"""

_SCALAR_KEYS = [
    "timestamp", "device_name", "uptime_raw", "uptime_seconds", "firmware_version",
    "cpu_usage_pct", "memory_usage_pct", "lan_ip", "lan_subnet", "lan_mac",
    "pon_vendor", "pon_part_number", "temperature_c", "voltage_v",
    "tx_power_dbm", "rx_power_dbm", "bias_current_ma", "onu_state", "onu_id",
    "loid_status", "pon_bytes_sent", "pon_bytes_received", "pon_packets_sent",
    "pon_packets_received", "pon_unicast_sent", "pon_unicast_received",
    "pon_multicast_sent", "pon_multicast_received", "pon_broadcast_sent",
    "pon_broadcast_received", "pon_fec_errors", "pon_hec_errors",
    "pon_packets_dropped", "pon_pause_sent", "pon_pause_received",
    "lan_rx_pkt", "lan_rx_err", "lan_rx_drop", "lan_tx_pkt", "lan_tx_err",
    "lan_tx_drop", "gpon_serial", "gpon_loid",
    "name_servers", "ipv4_default_gw", "ipv6_default_gw",
    "wan_interface", "wan_vlan_id", "wan_conn_type", "wan_protocol",
    "wan_ip", "wan_gateway", "wan_status",
]


_MIGRATIONS = [
    "ALTER TABLE metrics ADD COLUMN name_servers TEXT",
    "ALTER TABLE metrics ADD COLUMN ipv4_default_gw TEXT",
    "ALTER TABLE metrics ADD COLUMN ipv6_default_gw TEXT",
    "ALTER TABLE metrics ADD COLUMN wan_interface TEXT",
    "ALTER TABLE metrics ADD COLUMN wan_vlan_id INTEGER",
    "ALTER TABLE metrics ADD COLUMN wan_conn_type TEXT",
    "ALTER TABLE metrics ADD COLUMN wan_protocol TEXT",
    "ALTER TABLE metrics ADD COLUMN wan_ip TEXT",
    "ALTER TABLE metrics ADD COLUMN wan_gateway TEXT",
    "ALTER TABLE metrics ADD COLUMN wan_status TEXT",
]


def db_init(conn: sqlite3.Connection) -> None:
    """Initialize DB schema and apply any pending migrations."""
    conn.executescript(_DDL)
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


def db_insert(conn: sqlite3.Connection, metrics: dict[str, Any]) -> int:
    """Insert metrics row and ARP entries, return row id."""
    row = {k: metrics.get(k) for k in _SCALAR_KEYS}
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    cur = conn.execute(
        f"INSERT INTO metrics ({cols}) VALUES ({placeholders})",
        list(row.values()),
    )
    row_id = cur.lastrowid
    for arp in metrics.get("arp_table", []):
        conn.execute(
            "INSERT INTO arp_table (metrics_id, ip, mac) VALUES (?, ?, ?)",
            (row_id, arp["ip"], arp["mac"]),
        )
    conn.commit()
    return row_id


# --- Exporters ---

def export_json(metrics: dict[str, Any]) -> None:
    """Write metrics as pretty JSON."""
    JSON_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")


def _prom_gauge(name: str, value: Any, labels: dict[str, str] | None = None) -> str:
    """Format single Prometheus gauge line."""
    if value is None:
        return ""
    label_str = ""
    if labels:
        pairs = ",".join(f'{k}="{v}"' for k, v in labels.items())
        label_str = f"{{{pairs}}}"
    return f"leoxgpon_{name}{label_str} {value}"


def export_prometheus(metrics: dict[str, Any]) -> None:
    """Write Prometheus text exposition format."""
    device = metrics.get("device_name", "leoxgpon")
    lbl = {"device": device}
    ts_comment = f"# scraped {metrics.get('timestamp', '')}"
    lines = [
        ts_comment,
        "# HELP leoxgpon_cpu_usage_pct CPU utilization percent",
        "# TYPE leoxgpon_cpu_usage_pct gauge",
        _prom_gauge("cpu_usage_pct", metrics.get("cpu_usage_pct"), lbl),
        "# HELP leoxgpon_memory_usage_pct Memory utilization percent",
        "# TYPE leoxgpon_memory_usage_pct gauge",
        _prom_gauge("memory_usage_pct", metrics.get("memory_usage_pct"), lbl),
        "# HELP leoxgpon_uptime_seconds Device uptime in seconds",
        "# TYPE leoxgpon_uptime_seconds counter",
        _prom_gauge("uptime_seconds", metrics.get("uptime_seconds"), lbl),
        "# HELP leoxgpon_temperature_celsius Optical module temperature",
        "# TYPE leoxgpon_temperature_celsius gauge",
        _prom_gauge("temperature_celsius", metrics.get("temperature_c"), lbl),
        "# HELP leoxgpon_voltage_volts Optical module supply voltage",
        "# TYPE leoxgpon_voltage_volts gauge",
        _prom_gauge("voltage_volts", metrics.get("voltage_v"), lbl),
        "# HELP leoxgpon_tx_power_dbm Optical transmit power dBm",
        "# TYPE leoxgpon_tx_power_dbm gauge",
        _prom_gauge("tx_power_dbm", metrics.get("tx_power_dbm"), lbl),
        "# HELP leoxgpon_rx_power_dbm Optical receive power dBm",
        "# TYPE leoxgpon_rx_power_dbm gauge",
        _prom_gauge("rx_power_dbm", metrics.get("rx_power_dbm"), lbl),
        "# HELP leoxgpon_bias_current_milliamps Laser bias current",
        "# TYPE leoxgpon_bias_current_milliamps gauge",
        _prom_gauge("bias_current_milliamps", metrics.get("bias_current_ma"), lbl),
        "# HELP leoxgpon_pon_bytes_sent_total PON bytes transmitted",
        "# TYPE leoxgpon_pon_bytes_sent_total counter",
        _prom_gauge("pon_bytes_sent_total", metrics.get("pon_bytes_sent"), lbl),
        "# HELP leoxgpon_pon_bytes_received_total PON bytes received",
        "# TYPE leoxgpon_pon_bytes_received_total counter",
        _prom_gauge("pon_bytes_received_total", metrics.get("pon_bytes_received"), lbl),
        "# HELP leoxgpon_pon_packets_sent_total PON packets transmitted",
        "# TYPE leoxgpon_pon_packets_sent_total counter",
        _prom_gauge("pon_packets_sent_total", metrics.get("pon_packets_sent"), lbl),
        "# HELP leoxgpon_pon_packets_received_total PON packets received",
        "# TYPE leoxgpon_pon_packets_received_total counter",
        _prom_gauge("pon_packets_received_total", metrics.get("pon_packets_received"), lbl),
        "# HELP leoxgpon_pon_unicast_sent_total PON unicast packets transmitted",
        "# TYPE leoxgpon_pon_unicast_sent_total counter",
        _prom_gauge("pon_unicast_sent_total", metrics.get("pon_unicast_sent"), lbl),
        "# HELP leoxgpon_pon_unicast_received_total PON unicast packets received",
        "# TYPE leoxgpon_pon_unicast_received_total counter",
        _prom_gauge("pon_unicast_received_total", metrics.get("pon_unicast_received"), lbl),
        "# HELP leoxgpon_pon_multicast_sent_total PON multicast packets transmitted",
        "# TYPE leoxgpon_pon_multicast_sent_total counter",
        _prom_gauge("pon_multicast_sent_total", metrics.get("pon_multicast_sent"), lbl),
        "# HELP leoxgpon_pon_multicast_received_total PON multicast packets received",
        "# TYPE leoxgpon_pon_multicast_received_total counter",
        _prom_gauge("pon_multicast_received_total", metrics.get("pon_multicast_received"), lbl),
        "# HELP leoxgpon_pon_broadcast_sent_total PON broadcast packets transmitted",
        "# TYPE leoxgpon_pon_broadcast_sent_total counter",
        _prom_gauge("pon_broadcast_sent_total", metrics.get("pon_broadcast_sent"), lbl),
        "# HELP leoxgpon_pon_broadcast_received_total PON broadcast packets received",
        "# TYPE leoxgpon_pon_broadcast_received_total counter",
        _prom_gauge("pon_broadcast_received_total", metrics.get("pon_broadcast_received"), lbl),
        "# HELP leoxgpon_pon_pause_sent_total PON pause frames transmitted",
        "# TYPE leoxgpon_pon_pause_sent_total counter",
        _prom_gauge("pon_pause_sent_total", metrics.get("pon_pause_sent"), lbl),
        "# HELP leoxgpon_pon_pause_received_total PON pause frames received",
        "# TYPE leoxgpon_pon_pause_received_total counter",
        _prom_gauge("pon_pause_received_total", metrics.get("pon_pause_received"), lbl),
        "# HELP leoxgpon_pon_fec_errors_total PON FEC errors",
        "# TYPE leoxgpon_pon_fec_errors_total counter",
        _prom_gauge("pon_fec_errors_total", metrics.get("pon_fec_errors"), lbl),
        "# HELP leoxgpon_pon_hec_errors_total PON HEC errors",
        "# TYPE leoxgpon_pon_hec_errors_total counter",
        _prom_gauge("pon_hec_errors_total", metrics.get("pon_hec_errors"), lbl),
        "# HELP leoxgpon_pon_packets_dropped_total PON packets dropped",
        "# TYPE leoxgpon_pon_packets_dropped_total counter",
        _prom_gauge("pon_packets_dropped_total", metrics.get("pon_packets_dropped"), lbl),
        "# HELP leoxgpon_lan_rx_packets_total LAN received packets",
        "# TYPE leoxgpon_lan_rx_packets_total counter",
        _prom_gauge("lan_rx_packets_total", metrics.get("lan_rx_pkt"), lbl),
        "# HELP leoxgpon_lan_tx_packets_total LAN transmitted packets",
        "# TYPE leoxgpon_lan_tx_packets_total counter",
        _prom_gauge("lan_tx_packets_total", metrics.get("lan_tx_pkt"), lbl),
        "# HELP leoxgpon_lan_rx_errors_total LAN receive errors",
        "# TYPE leoxgpon_lan_rx_errors_total counter",
        _prom_gauge("lan_rx_errors_total", metrics.get("lan_rx_err"), lbl),
        "# HELP leoxgpon_lan_tx_errors_total LAN transmit errors",
        "# TYPE leoxgpon_lan_tx_errors_total counter",
        _prom_gauge("lan_tx_errors_total", metrics.get("lan_tx_err"), lbl),
        "# HELP leoxgpon_lan_rx_drops_total LAN receive drops",
        "# TYPE leoxgpon_lan_rx_drops_total counter",
        _prom_gauge("lan_rx_drops_total", metrics.get("lan_rx_drop"), lbl),
        "# HELP leoxgpon_lan_tx_drops_total LAN transmit drops",
        "# TYPE leoxgpon_lan_tx_drops_total counter",
        _prom_gauge("lan_tx_drops_total", metrics.get("lan_tx_drop"), lbl),
    ]
    content = "\n".join(line for line in lines if line) + "\n"
    PROM_PATH.write_text(content, encoding="utf-8")


def export_zabbix(metrics: dict[str, Any]) -> None:
    """Write Zabbix sender JSON format for use with zabbix_sender."""
    host = metrics.get("device_name") or "leoxgpon"
    ts = int(datetime.now(timezone.utc).timestamp())

    key_map = {
        "cpu_usage_pct": "leoxgpon.cpu.usage",
        "memory_usage_pct": "leoxgpon.memory.usage",
        "uptime_seconds": "leoxgpon.uptime",
        "temperature_c": "leoxgpon.pon.temperature",
        "voltage_v": "leoxgpon.pon.voltage",
        "tx_power_dbm": "leoxgpon.pon.tx_power",
        "rx_power_dbm": "leoxgpon.pon.rx_power",
        "bias_current_ma": "leoxgpon.pon.bias_current",
        "onu_state": "leoxgpon.pon.onu_state",
        "loid_status": "leoxgpon.pon.loid_status",
        "pon_bytes_sent": "leoxgpon.pon.bytes_sent",
        "pon_bytes_received": "leoxgpon.pon.bytes_received",
        "pon_packets_sent": "leoxgpon.pon.packets_sent",
        "pon_packets_received": "leoxgpon.pon.packets_received",
        "pon_unicast_sent": "leoxgpon.pon.unicast_sent",
        "pon_unicast_received": "leoxgpon.pon.unicast_received",
        "pon_multicast_sent": "leoxgpon.pon.multicast_sent",
        "pon_multicast_received": "leoxgpon.pon.multicast_received",
        "pon_broadcast_sent": "leoxgpon.pon.broadcast_sent",
        "pon_broadcast_received": "leoxgpon.pon.broadcast_received",
        "pon_pause_sent": "leoxgpon.pon.pause_sent",
        "pon_pause_received": "leoxgpon.pon.pause_received",
        "pon_fec_errors": "leoxgpon.pon.fec_errors",
        "pon_hec_errors": "leoxgpon.pon.hec_errors",
        "pon_packets_dropped": "leoxgpon.pon.packets_dropped",
        "wan_status": "leoxgpon.wan.status",
        "wan_ip": "leoxgpon.wan.ip",
        "wan_vlan_id": "leoxgpon.wan.vlan_id",
        "lan_rx_pkt": "leoxgpon.lan.rx_packets",
        "lan_tx_pkt": "leoxgpon.lan.tx_packets",
        "lan_rx_err": "leoxgpon.lan.rx_errors",
        "lan_tx_err": "leoxgpon.lan.tx_errors",
        "lan_rx_drop": "leoxgpon.lan.rx_drops",
        "lan_tx_drop": "leoxgpon.lan.tx_drops",
    }

    data = []
    for metric_key, zabbix_key in key_map.items():
        val = metrics.get(metric_key)
        if val is not None:
            data.append({
                "host": host,
                "key": zabbix_key,
                "value": str(val),
                "clock": ts,
            })

    payload = {"request": "sender data", "data": data}
    ZABBIX_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# --- Main loop ---

_running = True


def _handle_signal(signum: int, _frame: Any) -> None:
    global _running
    log.info("signal %d received, stopping", signum)
    _running = False


def run_once(conn: sqlite3.Connection) -> dict[str, Any]:
    """Single collection+export cycle."""
    log.info("collecting metrics")
    metrics = collect_all()
    db_insert(conn, metrics)
    export_json(metrics)
    export_prometheus(metrics)
    export_zabbix(metrics)
    cpu = metrics.get("cpu_usage_pct")
    mem = metrics.get("memory_usage_pct")
    rx = metrics.get("rx_power_dbm")
    tx = metrics.get("tx_power_dbm")
    log.info("cpu=%s%% mem=%s%% rx_power=%sdBm tx_power=%sdBm", cpu, mem, rx, tx)
    return metrics


def main() -> None:
    """Entry point: parse args, run collection loop."""
    parser = argparse.ArgumentParser(description="LeoX GPON scraper service")
    parser.add_argument(
        "--interval", type=int, default=60,
        help="collection interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="collect once and exit",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    conn = sqlite3.connect(DB_PATH)
    db_init(conn)

    if args.once:
        run_once(conn)
        conn.close()
        return

    log.info("starting scraper loop, interval=%ds", args.interval)
    while _running:
        try:
            run_once(conn)
        except Exception as exc:
            log.error("collection error: %s", exc)
        if not _running:
            break
        for _ in range(args.interval):
            if not _running:
                break
            time.sleep(1)

    conn.close()
    log.info("scraper stopped")


if __name__ == "__main__":
    main()
