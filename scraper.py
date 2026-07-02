"""GPON terminal scraper - collects metrics from LeoX ONT and exports them."""

import argparse
import base64
import json
import logging
import re
import signal
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import (
    BaseHTTPRequestHandler,
    HTTPServer,
    ThreadingHTTPServer,
)
from pathlib import Path
from typing import Any

# Defaults; overridable via CLI args (set in main()).
BASE_URL = "http://192.168.100.1"
TIMEOUT = 10

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "leoxgpon.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("leoxgpon")

_scrape_lock = threading.Lock()
_running = True

# ---------------------------------------------------------------------------
# Minimal stdlib HTML DOM
# ---------------------------------------------------------------------------

class _Node:
    """Minimal HTML element node."""

    __slots__ = ("tag", "attrs", "_text", "children", "parent")

    _VOID = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    })

    def __init__(self, tag: str, attrs: list[tuple[str, str | None]], parent: "_Node | None" = None) -> None:
        self.tag = tag
        self.attrs: dict[str, str] = {k: (v or "") for k, v in attrs}
        self._text = ""
        self.children: list["_Node"] = []
        self.parent = parent

    def __getitem__(self, key: str) -> str:
        return self.attrs[key]

    def get_text(self, strip: bool = True) -> str:
        """Return concatenated text content of this node and all descendants."""
        parts = [self._text]
        for child in self.children:
            parts.append(child.get_text(strip=False))
        result = "".join(parts)
        return result.strip() if strip else result

    def find_all(self, tag: str, **attrs: str) -> list["_Node"]:
        """Return all descendant nodes matching tag and optional attribute filters."""
        out: list[_Node] = []
        for child in self.children:
            if child.tag == tag and all(child.attrs.get(k) == v for k, v in attrs.items()):
                out.append(child)
            out.extend(child.find_all(tag, **attrs))
        return out

    def find(self, tag: str, **attrs: str) -> "_Node | None":
        """Return first descendant matching tag and optional attribute filters."""
        for node in self.find_all(tag, **attrs):
            return node
        return None

    def next_sibling(self, tag: str) -> "_Node | None":
        """Return next sibling element with the given tag."""
        if not self.parent:
            return None
        siblings = self.parent.children
        try:
            idx = siblings.index(self)
        except ValueError:
            return None
        for sib in siblings[idx + 1:]:
            if sib.tag == tag:
                return sib
        return None


class _DOMBuilder(HTMLParser):
    """Build a _Node tree from raw HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._root = _Node("root", [])
        self._cur = self._root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        node = _Node(tag, attrs, self._cur)
        self._cur.children.append(node)
        if tag not in _Node._VOID:
            self._cur = node

    def handle_endtag(self, tag: str) -> None:
        cur = self._cur
        while cur.parent and cur.tag != tag.lower():
            cur = cur.parent
        if cur.tag == tag.lower() and cur.parent:
            self._cur = cur.parent

    def handle_data(self, data: str) -> None:
        self._cur._text += data

    @property
    def root(self) -> _Node:
        return self._root


def _parse_html(html: str) -> _Node:
    """Parse HTML string into a _Node tree."""
    builder = _DOMBuilder()
    builder.feed(html)
    return builder.root


# ---------------------------------------------------------------------------
# HTTP fetch (stdlib only)
# ---------------------------------------------------------------------------

_AUTH_HEADER = ""


def set_ont_config(base_url: str, user: str, password: str) -> None:
    """Set ONT base URL and basic-auth credentials for fetch()."""
    global BASE_URL, _AUTH_HEADER
    BASE_URL = base_url.rstrip("/")
    creds = base64.b64encode(f"{user}:{password}".encode()).decode()
    _AUTH_HEADER = "Basic " + creds


def fetch(path: str) -> _Node | None:
    """Fetch ONT page and return parsed DOM tree, or None on error."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers={"Authorization": _AUTH_HEADER})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return _parse_html(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError) as exc:
        log.error("fetch %s failed: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Metric extraction helpers
# ---------------------------------------------------------------------------

def _text(root: _Node, label: str) -> str:
    """Find th by label text, return adjacent td text."""
    for th in root.find_all("th"):
        if label.lower() in th.get_text().lower():
            td = th.next_sibling("td")
            if td:
                return td.get_text()
    return ""


def _float(val: str) -> float | None:
    """Extract first float from string."""
    m = re.search(r"[-+]?\d+\.?\d*", val)
    return float(m.group()) if m else None


def _int(val: str) -> int | None:
    """Extract first integer (sign-aware) from string."""
    m = re.search(r"[-+]?\d+", val)
    return int(m.group()) if m else None


def _uptime_seconds(raw: str) -> int | None:
    """Convert uptime string like '28 days, 5:00' to seconds."""
    if not raw:
        return None
    days = hours = minutes = 0
    m = re.search(r"(\d+)\s*day", raw)
    if m:
        days = int(m.group(1))
    m = re.search(r"(\d+):(\d+)", raw)
    if m:
        hours, minutes = int(m.group(1)), int(m.group(2))
    return days * 86400 + hours * 3600 + minutes * 60


# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------

def scrape_status() -> dict[str, Any]:
    """Scrape device status: uptime, CPU, memory, LAN, WAN info."""
    root = fetch("status.asp")
    if not root:
        return {}
    result: dict[str, Any] = {
        "device_name":       _text(root, "Device Name"),
        "uptime_raw":        _text(root, "Uptime"),
        "firmware_version":  _text(root, "Firmware Version"),
        "cpu_usage_pct":     _int(_text(root, "CPU Usage")),
        "memory_usage_pct":  _int(_text(root, "Memory Usage")),
        "lan_ip":            _text(root, "IP Address"),
        "lan_subnet":        _text(root, "Subnet Mask"),
        "lan_mac":           _text(root, "MAC Address"),
        "name_servers":      _text(root, "Name Servers"),
        "ipv4_default_gw":   _text(root, "IPv4 Default Gateway"),
        "ipv6_default_gw":   _text(root, "IPv6 Default Gateway"),
    }
    for row in root.find_all("tr"):
        cells = [td.get_text() for td in row.find_all("td")]
        if len(cells) >= 7 and cells[0] and cells[0] not in ("LAN",):
            result["wan_interface"] = cells[0]
            result["wan_vlan_id"]   = _int(cells[1])
            result["wan_conn_type"] = cells[2]
            result["wan_protocol"]  = cells[3]
            result["wan_ip"]        = cells[4]
            result["wan_gateway"]   = cells[5]
            result["wan_status"]    = cells[6]
            break
    return result


def scrape_pon_status() -> dict[str, Any]:
    """Scrape PON optical and GPON registration status."""
    root = fetch("status_pon.asp")
    if not root:
        return {}
    return {
        "pon_vendor":       _text(root, "Vendor Name"),
        "pon_part_number":  _text(root, "Part Number"),
        "temperature_c":    _float(_text(root, "Temperature")),
        "voltage_v":        _float(_text(root, "Voltage")),
        "tx_power_dbm":     _float(_text(root, "Tx Power")),
        "rx_power_dbm":     _float(_text(root, "Rx Power")),
        "bias_current_ma":  _float(_text(root, "Bias Current")),
        "onu_state":        _text(root, "ONU State"),
        "onu_id":           _text(root, "ONU ID"),
        "loid_status":      _text(root, "LOID Status"),
    }


def scrape_pon_stats() -> dict[str, Any]:
    """Scrape PON byte/packet counters."""
    root = fetch("admin/pon-stats.asp")
    if not root:
        return {}
    return {
        "pon_bytes_sent":          _int(_text(root, "Bytes Sent")),
        "pon_bytes_received":      _int(_text(root, "Bytes Received")),
        "pon_packets_sent":        _int(_text(root, "Packets Sent")),
        "pon_packets_received":    _int(_text(root, "Packets Received")),
        "pon_unicast_sent":        _int(_text(root, "Unicast Packets Sent")),
        "pon_unicast_received":    _int(_text(root, "Unicast Packets Received")),
        "pon_multicast_sent":      _int(_text(root, "Multicast Packets Sent")),
        "pon_multicast_received":  _int(_text(root, "Multicast Packets Received")),
        "pon_broadcast_sent":      _int(_text(root, "Broadcast Packets Sent")),
        "pon_broadcast_received":  _int(_text(root, "Broadcast Packets Received")),
        "pon_fec_errors":          _int(_text(root, "FEC Errors")),
        "pon_hec_errors":          _int(_text(root, "HEC Errors")),
        "pon_packets_dropped":     _int(_text(root, "Packets Dropped")),
        "pon_pause_sent":          _int(_text(root, "Pause Packets Sent")),
        "pon_pause_received":      _int(_text(root, "Pause Packets Received")),
    }


def scrape_interface_stats() -> dict[str, Any]:
    """Scrape LAN interface packet statistics."""
    root = fetch("stats.asp")
    if not root:
        return {}
    for row in root.find_all("tr"):
        cells = row.find_all("td")
        if cells and cells[0].get_text().upper() == "LAN":
            vals = [c.get_text() for c in cells]
            if len(vals) >= 7:
                return {
                    "lan_rx_pkt":  _int(vals[1]),
                    "lan_rx_err":  _int(vals[2]),
                    "lan_rx_drop": _int(vals[3]),
                    "lan_tx_pkt":  _int(vals[4]),
                    "lan_tx_err":  _int(vals[5]),
                    "lan_tx_drop": _int(vals[6]),
                }
    return {}


def scrape_gpon() -> dict[str, Any]:
    """Scrape GPON config: serial number, LOID."""
    root = fetch("gpon.asp")
    if not root:
        return {}
    serial = ""
    for th in root.find_all("th"):
        if "serial" in th.get_text().lower():
            td = th.next_sibling("td")
            if td:
                serial = td.get_text()
    loid_input = root.find("input", name="fmgpon_loid")
    loid = loid_input["value"] if loid_input else ""
    return {"gpon_serial": serial, "gpon_loid": loid}


def scrape_arp() -> list[dict[str, str]]:
    """Scrape ARP table entries."""
    root = fetch("arptable.asp")
    if not root:
        return []
    entries = []
    for row in root.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            ip  = cells[0].get_text()
            mac = cells[1].get_text()
            if ip and mac:
                entries.append({"ip": ip, "mac": mac})
    return entries


def collect_all() -> dict[str, Any]:
    """Collect all metrics and return unified dict."""
    metrics: dict[str, Any] = {"timestamp": datetime.now(timezone.utc).isoformat()}
    metrics.update(scrape_status())
    metrics.update(scrape_pon_status())
    metrics.update(scrape_pon_stats())
    metrics.update(scrape_interface_stats())
    metrics.update(scrape_gpon())
    metrics["uptime_seconds"] = _uptime_seconds(metrics.get("uptime_raw", ""))
    metrics["arp_table"] = scrape_arp()
    return metrics


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    device_name TEXT, uptime_raw TEXT, uptime_seconds INTEGER,
    firmware_version TEXT, cpu_usage_pct INTEGER, memory_usage_pct INTEGER,
    lan_ip TEXT, lan_subnet TEXT, lan_mac TEXT,
    pon_vendor TEXT, pon_part_number TEXT,
    temperature_c REAL, voltage_v REAL, tx_power_dbm REAL,
    rx_power_dbm REAL, bias_current_ma REAL,
    onu_state TEXT, onu_id TEXT, loid_status TEXT,
    pon_bytes_sent INTEGER, pon_bytes_received INTEGER,
    pon_packets_sent INTEGER, pon_packets_received INTEGER,
    pon_unicast_sent INTEGER, pon_unicast_received INTEGER,
    pon_multicast_sent INTEGER, pon_multicast_received INTEGER,
    pon_broadcast_sent INTEGER, pon_broadcast_received INTEGER,
    pon_fec_errors INTEGER, pon_hec_errors INTEGER,
    pon_packets_dropped INTEGER, pon_pause_sent INTEGER, pon_pause_received INTEGER,
    lan_rx_pkt INTEGER, lan_rx_err INTEGER, lan_rx_drop INTEGER,
    lan_tx_pkt INTEGER, lan_tx_err INTEGER, lan_tx_drop INTEGER,
    gpon_serial TEXT, gpon_loid TEXT, name_servers TEXT,
    ipv4_default_gw TEXT, ipv6_default_gw TEXT,
    wan_interface TEXT, wan_vlan_id INTEGER, wan_conn_type TEXT,
    wan_protocol TEXT, wan_ip TEXT, wan_gateway TEXT, wan_status TEXT
);

CREATE TABLE IF NOT EXISTS arp_table (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metrics_id INTEGER NOT NULL REFERENCES metrics(id) ON DELETE CASCADE,
    ip TEXT NOT NULL, mac TEXT NOT NULL
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
    "lan_tx_drop", "gpon_serial", "gpon_loid", "name_servers",
    "ipv4_default_gw", "ipv6_default_gw", "wan_interface", "wan_vlan_id",
    "wan_conn_type", "wan_protocol", "wan_ip", "wan_gateway", "wan_status",
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
    """Initialize DB schema and apply pending migrations."""
    conn.executescript(_DDL)
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.commit()


def db_insert(conn: sqlite3.Connection, metrics: dict[str, Any]) -> int:
    """Insert metrics row and ARP entries, return row id."""
    row = {k: metrics.get(k) for k in _SCALAR_KEYS}
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    cur = conn.execute(f"INSERT INTO metrics ({cols}) VALUES ({placeholders})", list(row.values()))
    row_id = cur.lastrowid
    for arp in metrics.get("arp_table", []):
        conn.execute("INSERT INTO arp_table (metrics_id, ip, mac) VALUES (?, ?, ?)",
                     (row_id, arp["ip"], arp["mac"]))
    conn.commit()
    return row_id


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_json(metrics: dict[str, Any]) -> str:
    """Render metrics as pretty-printed JSON string."""
    return json.dumps(metrics, indent=2)


# (metric_name, source_key, prom_type, help_text)
_PROM_METRICS: list[tuple[str, str, str, str]] = [
    ("cpu_usage_pct", "cpu_usage_pct", "gauge",
     "CPU utilization percent"),
    ("memory_usage_pct", "memory_usage_pct", "gauge",
     "Memory utilization percent"),
    ("uptime_seconds", "uptime_seconds", "counter",
     "Device uptime in seconds"),
    ("temperature_celsius", "temperature_c", "gauge",
     "Optical module temperature"),
    ("voltage_volts", "voltage_v", "gauge",
     "Optical module supply voltage"),
    ("tx_power_dbm", "tx_power_dbm", "gauge",
     "Optical transmit power dBm"),
    ("rx_power_dbm", "rx_power_dbm", "gauge",
     "Optical receive power dBm"),
    ("bias_current_milliamps", "bias_current_ma", "gauge",
     "Laser bias current"),
    ("pon_bytes_sent_total", "pon_bytes_sent", "counter",
     "PON bytes transmitted"),
    ("pon_bytes_received_total", "pon_bytes_received", "counter",
     "PON bytes received"),
    ("pon_packets_sent_total", "pon_packets_sent", "counter",
     "PON packets transmitted"),
    ("pon_packets_received_total", "pon_packets_received", "counter",
     "PON packets received"),
    ("pon_unicast_sent_total", "pon_unicast_sent", "counter",
     "PON unicast packets transmitted"),
    ("pon_unicast_received_total", "pon_unicast_received", "counter",
     "PON unicast packets received"),
    ("pon_multicast_sent_total", "pon_multicast_sent", "counter",
     "PON multicast packets transmitted"),
    ("pon_multicast_received_total", "pon_multicast_received", "counter",
     "PON multicast packets received"),
    ("pon_broadcast_sent_total", "pon_broadcast_sent", "counter",
     "PON broadcast packets transmitted"),
    ("pon_broadcast_received_total", "pon_broadcast_received", "counter",
     "PON broadcast packets received"),
    ("pon_pause_sent_total", "pon_pause_sent", "counter",
     "PON pause frames transmitted"),
    ("pon_pause_received_total", "pon_pause_received", "counter",
     "PON pause frames received"),
    ("pon_fec_errors_total", "pon_fec_errors", "counter",
     "PON FEC errors"),
    ("pon_hec_errors_total", "pon_hec_errors", "counter",
     "PON HEC errors"),
    ("pon_packets_dropped_total", "pon_packets_dropped", "counter",
     "PON packets dropped"),
    ("lan_rx_packets_total", "lan_rx_pkt", "counter",
     "LAN received packets"),
    ("lan_tx_packets_total", "lan_tx_pkt", "counter",
     "LAN transmitted packets"),
    ("lan_rx_errors_total", "lan_rx_err", "counter",
     "LAN receive errors"),
    ("lan_tx_errors_total", "lan_tx_err", "counter",
     "LAN transmit errors"),
    ("lan_rx_drops_total", "lan_rx_drop", "counter",
     "LAN receive drops"),
    ("lan_tx_drops_total", "lan_tx_drop", "counter",
     "LAN transmit drops"),
]


def _prom_escape(val: str) -> str:
    """Escape a Prometheus label value."""
    return val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_prometheus(metrics: dict[str, Any]) -> str:
    """Render Prometheus text exposition format string."""
    device = _prom_escape(str(metrics.get("device_name", "leoxgpon")))
    lines = [f"# scraped {metrics.get('timestamp', '')}"]
    for name, key, ptype, help_text in _PROM_METRICS:
        value = metrics.get(key)
        if value is None:
            continue
        lines.append(f"# HELP leoxgpon_{name} {help_text}")
        lines.append(f"# TYPE leoxgpon_{name} {ptype}")
        lines.append(f'leoxgpon_{name}{{device="{device}"}} {value}')
    return "\n".join(lines) + "\n"


def render_zabbix(metrics: dict[str, Any]) -> str:
    """Render Zabbix sender JSON format string."""
    host = metrics.get("device_name") or "leoxgpon"
    ts = int(datetime.now(timezone.utc).timestamp())
    key_map = {
        "cpu_usage_pct":       "leoxgpon.cpu.usage",
        "memory_usage_pct":    "leoxgpon.memory.usage",
        "uptime_seconds":      "leoxgpon.uptime",
        "temperature_c":       "leoxgpon.pon.temperature",
        "voltage_v":           "leoxgpon.pon.voltage",
        "tx_power_dbm":        "leoxgpon.pon.tx_power",
        "rx_power_dbm":        "leoxgpon.pon.rx_power",
        "bias_current_ma":     "leoxgpon.pon.bias_current",
        "onu_state":           "leoxgpon.pon.onu_state",
        "loid_status":         "leoxgpon.pon.loid_status",
        "pon_bytes_sent":      "leoxgpon.pon.bytes_sent",
        "pon_bytes_received":  "leoxgpon.pon.bytes_received",
        "pon_packets_sent":    "leoxgpon.pon.packets_sent",
        "pon_packets_received":"leoxgpon.pon.packets_received",
        "pon_unicast_sent":    "leoxgpon.pon.unicast_sent",
        "pon_unicast_received":"leoxgpon.pon.unicast_received",
        "pon_multicast_sent":  "leoxgpon.pon.multicast_sent",
        "pon_multicast_received":"leoxgpon.pon.multicast_received",
        "pon_broadcast_sent":  "leoxgpon.pon.broadcast_sent",
        "pon_broadcast_received":"leoxgpon.pon.broadcast_received",
        "pon_pause_sent":      "leoxgpon.pon.pause_sent",
        "pon_pause_received":  "leoxgpon.pon.pause_received",
        "pon_fec_errors":      "leoxgpon.pon.fec_errors",
        "pon_hec_errors":      "leoxgpon.pon.hec_errors",
        "pon_packets_dropped": "leoxgpon.pon.packets_dropped",
        "wan_status":          "leoxgpon.wan.status",
        "wan_ip":              "leoxgpon.wan.ip",
        "wan_vlan_id":         "leoxgpon.wan.vlan_id",
        "lan_rx_pkt":          "leoxgpon.lan.rx_packets",
        "lan_tx_pkt":          "leoxgpon.lan.tx_packets",
        "lan_rx_err":          "leoxgpon.lan.rx_errors",
        "lan_tx_err":          "leoxgpon.lan.tx_errors",
        "lan_rx_drop":         "leoxgpon.lan.rx_drops",
        "lan_tx_drop":         "leoxgpon.lan.tx_drops",
    }
    data = [
        {"host": host, "key": zk, "value": str(metrics[mk]), "clock": ts}
        for mk, zk in key_map.items()
        if metrics.get(mk) is not None
    ]
    return json.dumps({"request": "sender data", "data": data}, indent=2)


# ---------------------------------------------------------------------------
# HTTP server (live scrape per request)
# ---------------------------------------------------------------------------

class _MetricsHandler(BaseHTTPRequestHandler):
    """Serve live ONT metrics scraped on each request."""

    _routes: dict[str, tuple[str, str]] = {
        "/metrics":      ("text/plain; version=0.0.4; charset=utf-8", "prometheus"),
        "/metrics.json": ("application/json", "json"),
        "/zabbix.json":  ("application/json", "zabbix"),
    }

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._respond(200, "text/plain", b"ok\n")
            return
        if path not in self._routes:
            self._respond(404, "text/plain", b"not found\n")
            return
        content_type, fmt = self._routes[path]
        with _scrape_lock:
            metrics = collect_all()
        match fmt:
            case "prometheus": body = render_prometheus(metrics).encode()
            case "json":       body = render_json(metrics).encode()
            case "zabbix":     body = render_zabbix(metrics).encode()
            case _:            body = b""
        self._respond(200, content_type, body)

    def _respond(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("http %s", fmt % args)


def start_http_server(host: str, port: int) -> HTTPServer:
    """Start HTTP metrics server in a daemon thread, return server instance."""
    server = ThreadingHTTPServer((host, port), _MetricsHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("http metrics server listening on %s:%d", host or "0.0.0.0", port)
    return server


# ---------------------------------------------------------------------------
# DB interval thread
# ---------------------------------------------------------------------------

def _db_loop(db_path: Path, interval: int) -> None:
    """Periodically scrape and persist to SQLite.

    Connection created here: sqlite3 objects must stay on one thread.
    """
    conn = sqlite3.connect(db_path)
    db_init(conn)
    try:
        while _running:
            try:
                with _scrape_lock:
                    metrics = collect_all()
                db_insert(conn, metrics)
                log.info(
                    "db dump: cpu=%s%% mem=%s%% rx=%sdBm tx=%sdBm",
                    metrics.get("cpu_usage_pct"),
                    metrics.get("memory_usage_pct"),
                    metrics.get("rx_power_dbm"),
                    metrics.get("tx_power_dbm"),
                )
            except sqlite3.Error as exc:
                log.error("db loop error: %s", exc)
            for _ in range(interval):
                if not _running:
                    break
                time.sleep(1)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Signal handling + entry point
# ---------------------------------------------------------------------------

def _handle_signal(signum: int, _frame: Any) -> None:
    global _running
    log.info("signal %d received, stopping", signum)
    _running = False


def main() -> None:
    """Parse args, start HTTP server and optional DB loop."""
    parser = argparse.ArgumentParser(description="LeoX GPON scraper service")
    parser.add_argument("--interval", type=int, default=60,
                        help="DB dump interval in seconds (default: 60)")
    parser.add_argument("--port", type=int, default=9101,
                        help="HTTP metrics server port (default: 9101, 0 to disable)")
    parser.add_argument("--host", type=str, default="",
                        help="HTTP server bind address (default: all interfaces)")
    parser.add_argument("--no-db", action="store_true",
                        help="disable SQLite persistence and interval scraping")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                        help=f"SQLite DB path (default: {DEFAULT_DB_PATH})")
    parser.add_argument("--ont-url", type=str, default=BASE_URL,
                        help="ONT web UI base URL")
    parser.add_argument("--ont-user", type=str, default="leox",
                        help="ONT basic-auth username")
    parser.add_argument("--ont-pass", type=str, default="leolabs_7",
                        help="ONT basic-auth password")
    args = parser.parse_args()

    set_ont_config(args.ont_url, args.ont_user, args.ont_pass)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.port:
        start_http_server(args.host, args.port)

    db_thread: threading.Thread | None = None
    if not args.no_db:
        args.db.parent.mkdir(parents=True, exist_ok=True)
        db_thread = threading.Thread(
            target=_db_loop, args=(args.db, args.interval), daemon=True,
        )
        db_thread.start()
        log.info("db loop started, interval=%ds, db=%s",
                 args.interval, args.db)

    while _running:
        time.sleep(1)

    if db_thread is not None:
        db_thread.join(timeout=5)
    log.info("scraper stopped")


if __name__ == "__main__":
    main()
