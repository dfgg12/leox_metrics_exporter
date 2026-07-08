# leoxgpon

Scraper service for LeoX GPON ONT devices. Polls the ONT web interface and
exposes metrics over HTTP in Prometheus, JSON, and Zabbix formats, with
optional SQLite persistence and file exports.

Runs on plain Linux (systemd) or on OpenWrt with a built-in LuCI status tab
(`luci-app-leoxgpon` package).

Highlights:
- Zero third-party Python dependencies - stdlib only (`urllib`, `html.parser`)
- Live scrape per HTTP request, serialized to avoid hammering the ONT
- Background SQLite persistence at a configurable interval
- OpenWrt: procd-managed service reading config from UCI, LuCI dashboard

Current release: **1.0.0-4** (see [CHANGELOG.md](CHANGELOG.md)). Planned
work is tracked in [ROADMAP.md](ROADMAP.md).

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

> The metrics endpoints are unauthenticated and expose device details
> (GPON serial, LOID, ARP table). On a router the LuCI proxy reaches them
> over `127.0.0.1`, so you can set `http_host '127.0.0.1'` to keep port
> 9101 off the LAN entirely. Leave the default `0.0.0.0` only when a
> remote Prometheus/Zabbix must scrape it, and firewall the port to
> trusted collectors.

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
configurable interval and inserts rows into the DB (default
`data/leoxgpon.db`, override with `--db`). Each dump also writes
`metrics.json`, `metrics.prom`, and `zabbix.json` files to `data/` unless
`--no-files` is given.

Tables:
- `metrics` - one row per scrape, all scalar fields
- `arp_table` - normalized ARP entries linked to each `metrics` row

## Requirements

- Python 3.11+ (stdlib only, no pip packages)
- On OpenWrt: `python3` and `python3-sqlite3` packages (without
  `python3-sqlite3` the scraper still runs, DB persistence auto-disables)

## Usage

```
python3 scraper.py [OPTIONS]

Options:
  --port INT      HTTP server port (default: 9101, 0 to disable)
  --host STR      Bind address (default: all interfaces)
  --interval INT  SQLite dump interval in seconds (default: 60)
  --no-db         Disable SQLite persistence
  --no-files      Skip writing metric files to disk on each DB dump
  --db PATH       SQLite DB path (default: data/leoxgpon.db)
  --ont-url URL   ONT web UI base URL (default: http://192.168.100.1)
  --ont-user STR  ONT basic-auth username (default: leox)
  --ont-pass STR  ONT basic-auth password (default: leolabs_7)
```

### Run directly

```sh
python3 scraper.py --interval 30
```

### Run as systemd service (Linux)

The unit runs the scraper from `/opt/leoxgpon` (immutable copy, decoupled
from the git checkout) and stores the DB in `/var/lib/leoxgpon`:

```sh
mkdir -p /opt/leoxgpon /var/lib/leoxgpon
cp scraper.py /opt/leoxgpon/
cp leoxgpon-scraper.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now leoxgpon-scraper
```

To deploy a new scraper version: `cp scraper.py /opt/leoxgpon/ &&
systemctl restart leoxgpon-scraper`.

### Prometheus scrape config

```yaml
scrape_configs:
  - job_name: leoxgpon
    static_configs:
      - targets: ["<host>:9101"]
    metrics_path: /metrics
```

`leoxgpon_uptime_seconds` is a gauge (it resets to 0 on reboot); every
`*_total` metric is a cumulative counter intended for `rate()` /
`increase()`.

### Zabbix sender

```sh
# Feed to Zabbix server directly
curl -sf http://localhost:9101/zabbix.json | zabbix_sender -z <zabbix-server> -i -
```

## OpenWrt / LuCI

The `luci-app-leoxgpon/` directory packages the scraper for OpenWrt with a
**Status > GPON Status** LuCI tab (30-second auto-refresh). Data is fetched
via the LuCI backend (Lua controller proxies to port 9101), so the browser
never needs direct access to the scraper port.

The LuCI view uses only theme-native components (`cbi-section`,
`cbi-rowstyle-*` zebra rows, `cbi-progressbar`), so it renders correctly
in light and dark mode with the Bootstrap theme.

### Package structure

```
luci-app-leoxgpon/
  Makefile                              OpenWrt build system entry point
  luasrc/controller/leoxgpon.lua        LuCI menu registration + /data proxy
  luasrc/view/leoxgpon/status.htm       HTML/JS dashboard view
  root/etc/init.d/leoxgpon             procd service script
  root/etc/config/leoxgpon             UCI default configuration
  root/usr/bin/leoxgpon                Shell shim -> python3 scraper.py
scripts/
  build-ipk.sh                          standalone .ipk builder (no buildroot)
```

### Recommended install: prebuilt ipk

Build the package on any Linux host (no OpenWrt buildroot needed) and
install it with opkg. This keeps every file package-owned and upgradeable:

```sh
ROUTER=192.168.1.1

./scripts/build-ipk.sh
scp -O bin/luci-app-leoxgpon_*_all.ipk root@$ROUTER:/tmp/
ssh root@$ROUTER "opkg update && opkg install /tmp/luci-app-leoxgpon_*_all.ipk"
```

The package:
- depends on `luci-base`, `python3`, `python3-sqlite3`, `curl`
  (opkg installs them automatically)
- marks `/etc/config/leoxgpon` as a conffile - your edited config survives
  upgrades (the pristine default lands next to it as `leoxgpon-opkg`)
- enables and starts the service and clears the LuCI cache on install
- stops and disables the service on removal

Upgrades: bump `PKG_RELEASE` in `luci-app-leoxgpon/Makefile`, rebuild,
`opkg install` the new ipk.

Open LuCI in the browser. **Status > GPON Status** will appear.

### Manual install (development)

For quick iteration without packaging, copy files directly:

```sh
ROUTER=192.168.1.1

ssh root@$ROUTER "opkg update && opkg install python3 python3-sqlite3"
ssh root@$ROUTER mkdir -p /usr/lib/leoxgpon /usr/lib/lua/luci/view/leoxgpon

scp -O scraper.py root@$ROUTER:/usr/lib/leoxgpon/scraper.py
scp -O luci-app-leoxgpon/luasrc/controller/leoxgpon.lua \
    root@$ROUTER:/usr/lib/lua/luci/controller/leoxgpon.lua
scp -O luci-app-leoxgpon/luasrc/view/leoxgpon/status.htm \
    root@$ROUTER:/usr/lib/lua/luci/view/leoxgpon/status.htm
scp -O luci-app-leoxgpon/root/etc/init.d/leoxgpon \
    root@$ROUTER:/etc/init.d/leoxgpon
scp -O luci-app-leoxgpon/root/etc/config/leoxgpon \
    root@$ROUTER:/etc/config/leoxgpon
scp -O luci-app-leoxgpon/root/usr/bin/leoxgpon \
    root@$ROUTER:/usr/bin/leoxgpon

ssh root@$ROUTER "chmod +x /etc/init.d/leoxgpon /usr/bin/leoxgpon"
ssh root@$ROUTER "/etc/init.d/leoxgpon enable && /etc/init.d/leoxgpon start"
ssh root@$ROUTER "rm -rf /tmp/luci-indexcache /tmp/luci-modulecache"
```

Note: manually copied files are invisible to opkg; prefer the ipk for
anything long-lived. Router lacking `sftp-server` needs the `scp -O`
(legacy protocol) flag shown above.

### UCI configuration

Settings live in `/etc/config/leoxgpon`:

```
config leoxgpon 'main'
    option ont_url      'http://192.168.100.1'   # ONT address
    option ont_user     'leox'                    # HTTP Basic Auth user
    option ont_pass     'leolabs_7'               # HTTP Basic Auth password
    option http_port    '9101'                    # metrics HTTP port
    option http_host    '0.0.0.0'                 # bind address
    option db_interval  '60'                      # SQLite dump interval (s)
    option db_enabled   '1'                       # 0 to disable SQLite
```

The init script passes these to the scraper (with `--no-files`, since the
DB lives on tmpfs and LuCI reads metrics over live HTTP, so the file
exports are unused on-router); the DB is written to
`/var/lib/leoxgpon/leoxgpon.db` (tmpfs, avoids flash wear).

Edit with `uci` or directly, then reload:

```sh
uci set leoxgpon.main.db_interval=30
uci commit leoxgpon
/etc/init.d/leoxgpon reload
```

### Building with the OpenWrt buildroot (alternative)

If you already run a full OpenWrt build system, the package Makefile is
buildroot-compatible:

```sh
# In feeds.conf
src-git leoxgpon https://github.com/yourrepo/leoxgpon.git

./scripts/feeds update leoxgpon
./scripts/feeds install luci-app-leoxgpon
make package/luci-app-leoxgpon/compile
```

For everything else `scripts/build-ipk.sh` is simpler and produces an
equivalent `Architecture: all` package.

## Architecture

```
             LeoX GPON ONT (192.168.100.1)
                  ^  HTTP Basic Auth, 6 pages
                  |
            scraper.py (stdlib-only DOM parser)
             |            |             |
   HTTP :9101       SQLite thread   optional file exports
   live scrape      (60s interval)  (metrics.json/.prom, zabbix.json)
   per request
     |    \
Prometheus  LuCI Lua controller (curl proxy)
Zabbix           |
Grafana     Status > GPON Status page
```

- One scrape lock serializes all ONT access - the device CPU is weak and
  concurrent scrapes would skew its own CPU metric.
- The HTTP server is threaded, so `/health` responds even while a slow
  ONT scrape is in progress.
- The SQLite connection lives entirely inside the DB thread
  (sqlite3 objects are not thread-safe across threads).
- On OpenWrt the DB sits on tmpfs (`/var/lib` -> `/tmp`): no flash wear,
  history resets on reboot. On plain Linux `/var/lib/leoxgpon` is
  persistent.

## Troubleshooting

- No data in LuCI tab: check `logread | grep leoxgpon` and
  `curl http://127.0.0.1:9101/health` on the router.
- `ModuleNotFoundError: sqlite3`: install `python3-sqlite3`; without it
  the scraper still serves HTTP but skips DB persistence (logs a warning).
- Empty metrics: verify ONT reachability and credentials with
  `curl -u leox:leolabs_7 http://192.168.100.1/status.asp`.
- LuCI tab missing after install: clear the cache -
  `rm -rf /tmp/luci-indexcache /tmp/luci-modulecache`.
- Gaps in Grafana: the exporter scrapes live per request; a scrape takes
  up to a few seconds. Set the Prometheus `scrape_timeout` to 15s or more.

## Credentials

ONT address and credentials default to the LeoX factory values
(`leox` / `leolabs_7`); override with `--ont-*` flags or UCI options.
