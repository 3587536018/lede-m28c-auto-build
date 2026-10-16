"""Exercise the restored Lua client against an in-memory Docker HTTP socket.

The socket is mocked, while the complete vendored Lua library and controller run
unchanged under Lua 5.1, matching the firmware's LuCI compatibility runtime.
"""

import hashlib
import json
from pathlib import Path
import unittest
from urllib.parse import quote

try:
    from lupa.lua51 import LuaRuntime
except ImportError:
    LuaRuntime = None


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "package/luci-app-dockerman"
LIB = ROOT / "package/luci-lib-docker"


class VendoredSourceTests(unittest.TestCase):
    def test_upstream_files_are_exact_except_documented_makefile_adaptation(self):
        for package in (APP, LIB):
            manifest = json.loads((package / "UPSTREAM.json").read_text())
            self.assertEqual("af8a35c38a548a5319349e3ff8ff70820bb0a4b3", manifest["revision"])
            for entry in manifest["files"]:
                with self.subTest(package=package.name, path=entry["path"]):
                    data = (package / entry["path"]).read_bytes()
                    if entry["modified"]:
                        self.assertEqual("Makefile", entry["path"])
                        text = data.decode().replace(
                            "include $(TOPDIR)/feeds/luci/luci.mk", "include ../../luci.mk"
                        ).replace("PKG_RELEASE:=1\n", "")
                        if package == LIB:
                            text = text.replace("PKG_VERSION:=26.005.68630~af8a35c\n", "")
                        data = text.encode()
                    actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                    self.assertEqual(entry["git_blob_sha1"], actual)
            license_info = manifest["license_text"]
            self.assertEqual(license_info["sha256"], hashlib.sha256(
                (package / license_info["path"]).read_bytes()).hexdigest())

    def test_chinese_catalog_and_lua_compatibility_dependency_are_preserved(self):
        catalog = (APP / "po/zh_Hans/dockerman.po").read_text(encoding="utf-8")
        self.assertIn('msgid "Containers"\nmsgstr "容器"', catalog)
        self.assertIn('msgid "Images"\nmsgstr "镜像"', catalog)
        self.assertIn("+luci-compat", (APP / "Makefile").read_text())
        self.assertTrue((APP / "luasrc/view/dockerman/overview.htm").is_file())


SOCKET_HARNESS = r'''
requests = {}
responses = {}
configuration = {}
routes = {}
local function socket()
    local s = {writes = {}}
    function s:connect(path) self.path = path; return true end
    function s:send(data)
        self.writes[#self.writes + 1] = data
        return #data
    end
    function s:close() self.closed = true end
    function s:blocksource() return function() return nil end end
    function s:linesource()
        requests[#requests + 1] = self
        local uri = self.writes[1]:match('^[A-Z]+ ([^ ]+) ')
        self.uri = uri
        local version = uri:match('^/v1%.(%d+)/')
        local response = responses[uri] or {body = '{}', code = 200}
        -- Docker 29.1 rejects explicit API versions below 1.44. Its
        -- unversioned routes use the daemon's current API version.
        if version and tonumber(version) < 44 then
            response = {code = 400, body = '{"message":"client version too old"}'}
        end
        local lines = {
            'HTTP/1.1 ' .. tostring(response.code or 200) .. ' mock',
            'Content-Type: ' .. (response.content_type or 'application/json'),
            'Api-Version: 1.52',
            'Content-Length: ' .. #response.body,
            ''
        }
        local index = 0
        return function(buffer)
            if buffer then return response.body end
            index = index + 1
            return lines[index]
        end
    end
    return s
end
package.loaded['nixio.util'] = {}
package.loaded['nixio.fs'] = {access = function() return true end}
package.loaded['nixio'] = {
    socket = socket,
    connect = function(host, port)
        local s = socket(); s.host = host; s.port = port; return s
    end,
    fs = package.loaded['nixio.fs']
}
nixio = package.loaded['nixio']
luci = {util = {urlencode = python_urlencode}, model = {}}
package.loaded['luci.util'] = luci.util
package.loaded['luci.jsonc'] = {parse = python_parse, stringify = python_stringify}
luci.jsonc = package.loaded['luci.jsonc']
local ltn12 = {source = {}, sink = {}, pump = {}}
function ltn12.source.string(text)
    return function() local result = text; text = nil; return result end
end
function ltn12.source.cat(...)
    local sources = {...}; local index = 1
    return function()
        while sources[index] do
            local chunk = sources[index]()
            if chunk then return chunk end
            index = index + 1
        end
    end
end
function ltn12.sink.table(destination)
    return function(chunk)
        if chunk then destination[#destination + 1] = chunk end
        return true
    end
end
function ltn12.pump.all(source, sink)
    while true do
        local chunk = source()
        sink(chunk)
        if not chunk then return true end
    end
end
package.loaded['luci.ltn12'] = ltn12
local cursor = {}
function cursor:get(_, _, key) return configuration[key] end
function cursor:get_bool(_, _, key) return configuration[key] == true end
package.loaded['luci.model.uci'] = {cursor = function() return cursor end}
luci.model.uci = package.loaded['luci.model.uci']
'''


@unittest.skipUnless(LuaRuntime, "Lua 5.1 runtime checks require lupa")
class DockerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().python_parse = lambda text: self.to_lua(json.loads(text))
        self.lua.globals().python_stringify = lambda value: json.dumps(self.from_lua(value))
        self.lua.globals().python_urlencode = lambda value: quote(value, safe="")
        self.lua.execute(SOCKET_HARNESS)
        self.lua.globals().docker_library = self.lua.execute(
            (LIB / "luasrc/docker.lua").read_text(encoding="utf-8"))
        self.lua.execute("package.loaded['luci.docker'] = docker_library")

    def to_lua(self, value):
        if isinstance(value, dict):
            return self.lua.table_from({k: self.to_lua(v) for k, v in value.items()})
        if isinstance(value, list):
            return self.lua.table_from([self.to_lua(v) for v in value])
        return value

    def from_lua(self, value):
        if hasattr(value, "items"):
            items = dict(value.items())
            if items and set(items) == set(range(1, len(items) + 1)):
                return [self.from_lua(items[k]) for k in range(1, len(items) + 1)]
            return {k: self.from_lua(v) for k, v in items.items()}
        return value

    def respond(self, uri, body, code=200, content_type="application/json"):
        if content_type == "application/json":
            body = json.dumps(body)
        self.lua.globals().responses[uri] = self.to_lua(
            {"body": body, "code": code, "content_type": content_type})

    def load_model(self):
        self.lua.globals().docker_model = self.lua.execute(
            (APP / "luasrc/model/docker.lua").read_text(encoding="utf-8"))
        self.lua.execute("package.loaded['luci.model.docker'] = docker_model")

    def test_library_emits_unversioned_requests_accepted_by_docker_29(self):
        self.respond("/containers/json?all=true", [{"Id": "example", "State": "running"}])
        self.lua.execute("client = docker_library.new({socket_path='/var/run/docker.sock', version='v1.40'})")
        response = self.lua.eval("client.containers:list({query={all=true}})")
        self.assertEqual(200, response["code"])
        self.assertEqual("running", response["body"][1]["State"])
        request = self.lua.globals().requests[1]
        self.assertEqual("/containers/json?all=true", request["uri"])
        self.assertIn("Host: localhost\r\n", request["writes"][1])
        self.assertEqual("/var/run/docker.sock", request["path"])

    def test_dashboard_resources_decode_docker_responses(self):
        self.respond("/images/json", [{"Id": "sha256:image", "Size": 2048}])
        self.respond("/networks", [{"Name": "bridge"}])
        self.respond("/volumes", {"Volumes": [{"Name": "data"}]})
        self.respond("/info", {"ServerVersion": "29.1.1", "MemTotal": 2147483648})
        self.lua.execute("client = docker_library.new({socket_path='/var/run/docker.sock'})")
        self.assertEqual(2048, self.lua.eval("client.images:list().body[1].Size"))
        self.assertEqual("bridge", self.lua.eval("client.networks:list().body[1].Name"))
        self.assertEqual("data", self.lua.eval("client.volumes:list().body.Volumes[1].Name"))
        self.assertEqual("29.1.1", self.lua.eval("client:info().body.ServerVersion"))

    def test_container_creation_and_start_preserve_request_body(self):
        self.respond("/containers/create?name=example", {"Id": "created"}, 201)
        self.respond("/containers/created/start", "", 204, "text/plain")
        self.lua.execute("client = docker_library.new({socket_path='/var/run/docker.sock'})")
        response = self.lua.eval("client.containers:create({name='example', body={Image='alpine:latest', HostConfig={RestartPolicy={Name='unless-stopped'}}}})")
        self.assertEqual("created", response["body"]["Id"])
        request = self.lua.globals().requests[1]
        body = json.loads(request["writes"][2])
        self.assertEqual("unless-stopped", body["HostConfig"]["RestartPolicy"]["Name"])
        self.assertEqual(204, self.lua.eval("client.containers:start({id='created'}).code"))

    def test_archive_callback_receives_raw_data(self):
        self.respond("/containers/example/archive?path=/data", "archive-bytes", content_type="application/x-tar")
        self.lua.execute("client = docker_library.new({socket_path='/var/run/docker.sock'}); received = ''")
        response = self.lua.eval("client.containers:get_archive({id='example', query={path='/data'}}, function(response, source) received=source() end)")
        self.assertEqual(200, response["code"])
        self.assertEqual("archive-bytes", self.lua.globals().received)

    def test_model_preserves_custom_unix_socket(self):
        self.lua.globals().configuration["socket_path"] = "/run/custom-docker.sock"
        self.load_model()
        self.assertEqual(200, self.lua.eval("docker_model.new():_ping().code"))
        self.assertEqual("/run/custom-docker.sock", self.lua.globals().requests[1]["path"])

    def test_model_preserves_remote_endpoint(self):
        self.lua.globals().configuration = self.to_lua(
            {"remote_endpoint": True, "remote_host": "192.0.2.10", "remote_port": "2375"})
        self.load_model()
        self.assertEqual(200, self.lua.eval("docker_model.new():_ping().code"))
        request = self.lua.globals().requests[1]
        self.assertEqual("192.0.2.10", request["host"])
        self.assertEqual("2375", request["port"])
        self.assertIsNone(request["path"])

    def test_controller_restores_all_classic_navigation_tabs(self):
        self.load_model()
        self.lua.execute('''
            function entry(path, target, title, order)
                local route = {target=target, title=title, order=order}
                routes[table.concat(path, '/')] = route
                return route
            end
            function _(text) return text end
            function firstchild() return 'firstchild' end
            function cbi(name) return 'cbi:' .. name end
            function form(name) return 'form:' .. name end
            function call(name) return 'call:' .. name end
        ''')
        self.lua.execute((APP / "luasrc/controller/dockerman.lua").read_text(encoding="utf-8"))
        self.lua.execute("require('luci.controller.dockerman').index()")
        routes = self.lua.globals().routes
        for tab in ("config", "overview", "containers", "images", "networks", "volumes", "events"):
            with self.subTest(tab=tab):
                self.assertIsNotNone(routes["admin/docker/" + tab])
        self.assertEqual("form:dockerman/overview", routes["admin/docker/overview"]["target"])

    def test_all_vendored_lua_files_parse_with_firmware_lua_version(self):
        compile_lua = self.lua.eval("function(source, name) local fn, err=loadstring(source, name); return fn ~= nil, err end")
        for package in (APP, LIB):
            for path in package.rglob("*.lua"):
                with self.subTest(path=path):
                    ok, error = compile_lua(path.read_text(encoding="utf-8"), path.name)
                    self.assertTrue(ok, error)


if __name__ == "__main__":
    unittest.main()
