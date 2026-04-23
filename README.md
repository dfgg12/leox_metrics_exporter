# leoxgpon - OpenWrt / LuCI edition

OpenWrt package that scrapes metrics from a LeoX GPON ONT and exposes them
over HTTP, with a built-in LuCI tab in the router web interface showing live
data.

This branch (`openwrt-luci`) adds:
- `luci-app-leoxgpon` - OpenWrt package (Makefile + LuCI controller + view)
- Zero third-party Python dependencies - stdlib only (`urllib`, `html.parser`)
- `procd`-managed init.d service reading config from UCI
- LuCI status tab with 30-second auto-refresh

---

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

---

## HTTP endpoints

The scraper performs a live ONT scrape on every request (serialized).

| Endpoint | Content-Type | Description |
|---|---|---|
| `GET /metrics` | `text/plain` | Prometheus exposition format |
| `GET /metrics.json` | `application/json` | Full metrics as JSON |
| `GET /zabbix.json` | `application/json` | Zabbix sender batch payload |
| `GET /health` | `text/plain` | Returns `ok` if service is running |

Default port: **9101**.

---

## LuCI web interface

After installation, a **GPON Status** tab appears under **Status** in the
LuCI navigation menu.

The page is organized into sections:

```
Status > GPON Status
+-------------------------------------------------------+
| LeoX GPON Status              Updated 23:26:14  [Refresh] |
+-------------------------------------------------------+
| DEVICE                                                |
|   Name          LeoX-ONT                             |
|   Firmware      1.2.3                                |
|   Uptime        5 days, 3:42                         |
|   CPU           [####      ] 38%                     |
|   Memory        [###       ] 28%                     |
+-------------------------------------------------------+
| WAN                                                   |
|   Status        [CONNECTED]                          |
|   Interface     VEIP_0.1                             |
|   VLAN ID       100                                  |
|   IP Address    1.2.3.4                              |
|   Gateway       1.2.3.1                              |
+-------------------------------------------------------+
| PON OPTICAL                                           |
|   ONU State     [O5]                                 |
|   Serial        LEOX12345678                         |
|   Temperature   45.1 C                               |
|   TX Power      -3.50 dBm                            |
|   RX Power      -18.20 dBm                           |
+-------------------------------------------------------+
| LAN  |  PON COUNTERS  |  ARP TABLE                   |
+-------------------------------------------------------+
```

Data is fetched via the LuCI backend (Lua controller proxies to port 9101)
so the browser never needs direct access to the scraper port.

---

## Package structure

```
luci-app-leoxgpon/
  Makefile                              OpenWrt build system entry point
  luasrc/controller/leoxgpon.lua        LuCI menu registration + /data proxy
  luasrc/view/leoxgpon/status.htm       HTML/JS dashboard view
  root/etc/init.d/leoxgpon             procd service script
  root/etc/config/leoxgpon             UCI default configuration
  root/usr/bin/leoxgpon                Shell shim -> python3 scraper.py
```

---

## Requirements

- OpenWrt 21.02 or later
- `python3` package (`opkg install python3`)
- No other packages required - scraper uses Python stdlib only

---

## Setup

### 1. Install python3

```sh
opkg update
opkg install python3
```

### 2. Copy files to the router

From the repo root on your workstation:

```sh
ROUTER=192.168.1.1

# scraper
scp scraper.py root@$ROUTER:/usr/lib/leoxgpon/scraper.py

# LuCI controller and view
scp luci-app-leoxgpon/luasrc/controller/leoxgpon.lua \
    root@$ROUTER:/usr/lib/lua/luci/controller/leoxgpon.lua

ssh root@$ROUTER mkdir -p /usr/lib/lua/luci/view/leoxgpon
scp luci-app-leoxgpon/luasrc/view/leoxgpon/status.htm \
    root@$ROUTER:/usr/lib/lua/luci/view/leoxgpon/status.htm

# init.d service
scp luci-app-leoxgpon/root/etc/init.d/leoxgpon \
    root@$ROUTER:/etc/init.d/leoxgpon
ssh root@$ROUTER chmod +x /etc/init.d/leoxgpon

# UCI config (only if not already present)
scp luci-app-leoxgpon/root/etc/config/leoxgpon \
    root@$ROUTER:/etc/config/leoxgpon

# wrapper script
scp luci-app-leoxgpon/root/usr/bin/leoxgpon \
    root@$ROUTER:/usr/bin/leoxgpon
ssh root@$ROUTER chmod +x /usr/bin/leoxgpon
```

### 3. Enable and start the service

```sh
ssh root@$ROUTER "/etc/init.d/leoxgpon enable && /etc/init.d/leoxgpon start"
```

### 4. Clear LuCI cache

```sh
ssh root@$ROUTER "rm -rf /tmp/luci-indexcache /tmp/luci-modulecache"
```

Open LuCI in the browser. **Status > GPON Status** will appear.

---

## Configuration

Settings are stored in UCI at `/etc/config/leoxgpon`:

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

Edit with `uci` or directly in `/etc/config/leoxgpon`, then reload:

```sh
uci set leoxgpon.main.db_interval=30
uci commit leoxgpon
/etc/init.d/leoxgpon reload
```

---

## Building as an OpenWrt package

If you have an OpenWrt build system set up, add this repo as a feed and build
normally:

```sh
# In feeds.conf
src-git leoxgpon https://github.com/yourrepo/leoxgpon.git;openwrt-luci

./scripts/feeds update leoxgpon
./scripts/feeds install luci-app-leoxgpon
make package/luci-app-leoxgpon/compile V=s
```

The resulting `.ipk` installs via:

```sh
opkg install luci-app-leoxgpon_1.0.0-1_all.ipk
```

---

## Service management

```sh
/etc/init.d/leoxgpon start
/etc/init.d/leoxgpon stop
/etc/init.d/leoxgpon restart
/etc/init.d/leoxgpon reload    # re-reads UCI config without full restart

# Check status
/etc/init.d/leoxgpon status

# View logs
logread | grep leoxgpon
```
