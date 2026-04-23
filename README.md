# leoxgpon

Scraper service for LeoX GPON ONT devices. Polls the ONT web interface and
exposes metrics over HTTP in Prometheus, JSON, and Zabbix formats, with
optional SQLite persistence.

## Metrics collected

### Device
| Field | Description |
|---|---|
| `device_name` | ONT hostname |
| `firmware_version` | Firmware string |
| `uptime_raw` / `uptime_seconds` | Human string and seconds |
| `cpu_usage_pct` | CPU utilization % |
| `memory_usage_pct` | Memory utilization % |

### WAN
| Field | Description |
|---|---|
| `wan_interface` | Interface name |
| `wan_vlan_id` | VLAN tag |
| `wan_conn_type` | Connection type |
| `wan_protocol` | Protocol (PPPoE, IPoE, ...) |
| `wan_ip` | WAN IP address |
| `wan_gateway` | Default gateway |
| `wan_status` | Connection status |
| `ipv4_default_gw` / `ipv6_default_gw` | Gateway addresses |
| `name_servers` | DNS server list |

### PON optical
| Field | Description |
|---|---|
| `onu_state` | ONU registration state (O1-O5) |
| `onu_id` | Assigned ONU ID |
| `loid_status` | LOID authentication status |
| `gpon_serial` | ONT serial number |
| `gpon_loid` | Configured LOID |
| `pon_vendor` | SFP vendor name |
| `pon_part_number` | SFP part number |
| `temperature_c` | Module temperature (C) |
| `voltage_v` | Module supply voltage (V) |
| `tx_power_dbm` | Transmit optical power (dBm) |
| `rx_power_dbm` | Receive optical power (dBm) |
| `bias_current_ma` | Laser bias current (mA) |

### PON counters
Sent and received counts for: bytes, packets, unicast, multicast, broadcast,
pause frames. Plus: FEC errors, HEC errors, dropped packets.

### LAN
RX/TX packet counts, error counts, and drop counts. IP address, subnet mask,
MAC address.

### ARP table
All entries from the ONT ARP cache (`ip`, `mac`).

## HTTP endpoints

The scraper performs a live ONT scrape on every request (serialized to avoid
hammering the device).

| Endpoint | Content-Type | Description |
|---|---|---|
| `GET /metrics` | `text/plain` | Prometheus exposition format |
| `GET /metrics.json` | `application/json` | Full metrics as JSON |
| `GET /zabbix.json` | `application/json` | Zabbix sender batch payload |
| `GET /health` | `text/plain` | Returns `ok` if service is running |

Default port: **9101**.

### JSON example (abbreviated)

```json
{
  "timestamp": "2026-04-23T21:00:00+00:00",
  "device_name": "LeoX-ONT",
  "firmware_version": "1.2.3",
  "uptime_raw": "5 days, 3:42",
  "uptime_seconds": 444120,
  "cpu_usage_pct": 12,
  "memory_usage_pct": 38,
  "wan_ip": "1.2.3.4",
  "wan_status": "Connected",
  "onu_state": "O5",
  "tx_power_dbm": -3.5,
  "rx_power_dbm": -18.2,
  "temperature_c": 45.1,
  "arp_table": [
    {"ip": "192.168.100.2", "mac": "aa:bb:cc:dd:ee:ff"}
  ]
}
```

## SQLite persistence

When DB mode is enabled (default), a background thread scrapes the ONT at a
configurable interval and inserts rows into `data/leoxgpon.db`.

Tables:
- `metrics` - one row per scrape, all scalar fields
- `arp_table` - normalized ARP entries linked to each `metrics` row

## Requirements

- Python 3.11+
- `requests`
- `beautifulsoup4`

```
pip install requests beautifulsoup4
```

## Usage

```
python3 scraper.py [OPTIONS]

Options:
  --port INT      HTTP server port (default: 9101, 0 to disable)
  --host STR      Bind address (default: all interfaces)
  --interval INT  SQLite dump interval in seconds (default: 60)
  --no-db         Disable SQLite persistence
  --no-files      Skip writing metric files to disk on each DB dump
```

### Run directly

```sh
python3 scraper.py --interval 30
```

### Run as systemd service

```sh
cp leoxgpon-scraper.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now leoxgpon-scraper
```

### Prometheus scrape config

```yaml
scrape_configs:
  - job_name: leoxgpon
    static_configs:
      - targets: ["<host>:9101"]
    metrics_path: /metrics
```

### Zabbix sender

```sh
# Feed to Zabbix server directly
curl -sf http://localhost:9101/zabbix.json | zabbix_sender -z <zabbix-server> -i -
```

## Credentials

ONT address and credentials are hardcoded at the top of `scraper.py`:

```python
BASE_URL = "http://192.168.100.1"
AUTH = ("leox", "leolabs_7")
```
