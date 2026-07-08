# Changelog

All notable changes to `luci-app-leoxgpon` and the scraper service.
Format follows [Keep a Changelog](https://keepachangelog.com/); the
package version tracks `PKG_VERSION-PKG_RELEASE` from the Makefile.

## [1.0.0-4] - 2026-07-08

Reliability, security, and accessibility hardening. No config or schema
changes - drop-in upgrade over 1.0.0-3.

### Fixed
- Persistence daemon could die silently and permanently. The DB loop
  caught only `(OSError, ValueError, sqlite3.Error)`, so any other error
  (e.g. a parser `KeyError`) unwound the thread while the process stayed
  up and systemd never restarted it. The loop now survives unexpected
  scrape/field errors and keeps collecting history.
- `scrape_gpon` raised `KeyError` when the LOID `<input>` had no `value`
  attribute (root cause of the crash above). Now uses `attrs.get`.
- DOM-XSS in the LuCI dashboard: device-controlled `wan_status` and
  `onu_state` were injected into `innerHTML` unescaped. Both are now
  escaped.
- OpenWrt init script wrote `metrics.json/.prom/zabbix.json` to the
  read-write overlay every interval, wearing flash and contradicting the
  documented "no flash wear". It now runs with `--no-files` (LuCI reads
  live HTTP, so the exports were unused on-router).
- Prometheus `uptime_seconds` was typed `counter` but resets on reboot;
  corrected to `gauge`.
- Prometheus device label fell back to `leoxgpon` only on a missing key,
  not an empty value; now matches the Zabbix behaviour.
- SQLite migrations no longer swallow every `OperationalError`. `db_init`
  introspects existing columns (`PRAGMA table_info`) and adds only the
  missing ones, so genuine errors (locked/read-only DB) surface.

### Security
- LuCI controller validates `http_port` is numeric before building the
  metrics URL (defence in depth against a tampered UCI value).
- README documents that the `:9101` metrics endpoints are unauthenticated
  and how to restrict them (`http_host '127.0.0.1'` + firewall).

### Accessibility
- CPU/memory progress bars expose `role="progressbar"` and live
  `aria-valuenow`; the error banner uses `role="alert"`.
- The dashboard pauses polling when the tab is hidden and clears its
  interval on unload instead of leaking a timer.

## [1.0.0-3] - 2026-07-02

- LuCI status view uses the theme-native `cbi-progressbar` and a flex
  toolbar for the refresh control; renders correctly in light and dark.

## [1.0.0-2] - 2026-07-02

- Zebra-striped (`cbi-rowstyle-*`) row backgrounds on all status tables.

## [1.0.0-1] - 2026-07-02

- First packaged release: standalone `.ipk` build (`scripts/build-ipk.sh`,
  no buildroot required), `/opt/leoxgpon` systemd layout for plain Linux,
  procd service and LuCI **Status > GPON Status** tab for OpenWrt.
- Stdlib-only Python scraper (dropped `requests`/`bs4`): live scrape per
  HTTP request, background SQLite persistence, Prometheus/JSON/Zabbix
  exports, WAN fields and full PON packet counters.

[1.0.0-4]: https://github.com/dfgg12/leox_metrics_exporter/releases/tag/v1.0.0-4
[1.0.0-3]: https://github.com/dfgg12/leox_metrics_exporter/releases/tag/v1.0.0-3
[1.0.0-2]: https://github.com/dfgg12/leox_metrics_exporter/releases/tag/v1.0.0-2
[1.0.0-1]: https://github.com/dfgg12/leox_metrics_exporter/releases/tag/v1.0.0-1
