module("luci.controller.leoxgpon", package.seeall)

function index()
	entry({"admin", "status", "leoxgpon"},
		view("leoxgpon/status"), _("GPON Status"), 90)
	entry({"admin", "status", "leoxgpon", "data"},
		call("action_data")).leaf = true
end

function action_data()
	local uci  = require "luci.model.uci".cursor()
	local port = uci:get("leoxgpon", "main", "http_port") or "9101"
	local url  = "http://127.0.0.1:" .. port .. "/metrics.json"

	local result = luci.sys.exec(
		string.format("curl -sf --max-time 8 %q 2>/dev/null", url)
	)

	luci.http.prepare_content("application/json")
	if result and #result > 2 then
		luci.http.write(result)
	else
		luci.http.write('{"error":"scraper_unavailable","timestamp":null}')
	end
end
