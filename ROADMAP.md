# Roadmap

Planned work for `leoxgpon`, roughly ordered. Nothing here is started -
the current release (1.0.0-4) closed all known correctness, security, and
accessibility findings from the last audit. See [CHANGELOG.md](CHANGELOG.md)
for shipped changes.

## Near term (next release)

- **Optional auth / localhost default for `:9101`.** The metrics endpoints
  are unauthenticated. Today the mitigation is documented (bind
  `127.0.0.1`, firewall the port). Add a first-class option: bearer-token
  or basic-auth on `/metrics*`, or ship the OpenWrt default as
  `http_host '127.0.0.1'` since the LuCI proxy already uses localhost.
  Trade-off: a localhost default breaks remote Prometheus/Zabbix out of
  the box, so it must be opt-in-documented.
- **`--timeout` CLI flag.** `TIMEOUT` is fixed at 10s per page (up to ~60s
  for a hung ONT across six pages). Expose it as a flag and UCI option so
  slow links can tune it.
- **rpcd `acl.d` ACL file.** The classic Lua dispatcher gates the menu via
  session auth, which works, but a `root/usr/share/rpcd/acl.d/*.json`
  grant is needed for the JS-based (client) LuCI menu and finer-grained
  read-only access control.

## Medium term

- **Dashboard alerting.** Surface LAN/PON error and drop counters and
  optical-level thresholds (e.g. rx power out of range) as visual warnings
  in the LuCI tab, not just raw numbers.
- **Persistent history option on OpenWrt.** The DB lives on tmpfs (resets
  on reboot). Offer an opt-in path to persist to attached storage
  (USB/SD) with a retention/rotation policy to bound flash wear.
- **Delta/rate counters.** Counters are cumulative; add derived rate
  fields (or document the intended `rate()` usage) for packet/byte
  throughput so dashboards do not have to compute them.
- **Grafana dashboard + Zabbix template.** Ship a ready-to-import Grafana
  dashboard JSON and a Zabbix template matching the exported keys.

## Longer term / exploratory

- **Multi-WAN / multi-device.** The WAN scrape captures the first non-LAN
  row; generalise to multiple WAN interfaces and label metrics per
  interface. Consider scraping more than one ONT from a single service.
- **Resilience to firmware/page drift.** The scraper matches fields by
  label substring in DOM order. Add fixtures for known firmware variants
  and a small regression harness so page changes are caught early.
- **CI.** Lint (`ruff`/`pylint`), `py_compile`, and an ipk-build smoke
  check on push; attach the built ipk to tagged releases automatically.

## Non-goals

- Rewriting the stdlib-only scraper to depend on `requests`/`bs4`. The
  zero-dependency constraint is deliberate for OpenWrt.
- Writing to the ONT / changing its configuration. This is a read-only
  metrics exporter.
