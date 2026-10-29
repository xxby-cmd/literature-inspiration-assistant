"""Owner-authorized temporary reading gateway; loopback Worker stays private."""

import gzip
import concurrent.futures
import hmac
import http.client
import http.cookies
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

BACKEND = "http://127.0.0.1:" + str(int(os.environ.get("LITERATURE_WORKER_PORT", "18744")))
PORT = int(os.environ.get("LITERATURE_READER_PORT", "18743"))
RELAY_PORT = int(os.environ.get("LITERATURE_RELAY_PORT", "18745"))


def secret(store):
    from companion import protect, atomic

    path = store.home / "reader_gate.dpapi"
    if not path.exists():
        atomic(path, protect(secrets.token_urlsafe(32).encode()))
    return protect(path.read_bytes(), True).decode()


def safe_target(value):
    return value if re.fullmatch(r"/(?:\?read=[a-f0-9]{32})?", value or "") else "/"


def direct_entry_url(store, target="/"):
    site = store.meta("reader_site") or {}
    root = site.get("url", "").rstrip("/")
    if not root.startswith("https://"):
        return ""
    if site.get("mode") != "local":
        return root + safe_target(target)
    return (
        root + "/enter/" + secret(store) + "?" + urllib.parse.urlencode({"to": safe_target(target)})
    )


def entry_url(store, target="/"):
    if target == "/":
        from reader_portal import fixed_url

        fixed = fixed_url(store)
        if fixed:
            return fixed
    return direct_entry_url(store, target)


def local_entry_url(store, target="/"):
    return (
        f"http://127.0.0.1:{PORT}/enter/"
        + secret(store)
        + "?"
        + urllib.parse.urlencode({"to": safe_target(target)})
    )


def permitted(path, method):
    if method == "POST":
        return path in ("/api/action", "/api/discover", "/api/paper-state")
    return method == "GET" and (
        path
        in (
            "/",
            "/api/today",
            "/api/library",
            "/api/batches",
            "/api/batch",
            "/api/shelf",
            "/favicon.svg",
        )
        or bool(re.fullmatch("/api/node/[a-f0-9]{32}", path))
        or path.startswith(("/assets/", "/_vinext/", "/_next/static/"))
    )


def start_gateway(store, port=PORT, backend=BACKEND):
    key = secret(store)
    instance = (store.meta("reader_runtime") or {}).get("instance_id", "")
    cookie_name = "lc_reader_" + instance[:12] if instance else "lc_reader"

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    # The upstream is exclusively loopback; do not discover system proxies for
    # every asset/API request (measured at about 270 ms per request on this PC).
    upstream = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(
            self,
            status,
            body=b"",
            content_type="text/plain; charset=utf-8",
            headers=None,
            cache="no-store",
        ):
            compressed = False
            if (
                len(body) > 1024
                and "gzip" in self.headers.get("Accept-Encoding", "")
                and any(t in content_type for t in ("text/", "javascript", "json", "svg"))
            ):
                body = gzip.compress(body, compresslevel=5)
                compressed = True
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            if compressed:
                self.send_header("Content-Encoding", "gzip")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            host = self.headers.get("Host", "")
            public = urllib.parse.urlsplit((store.meta("reader_site") or {}).get("url", "")).netloc
            return host in (
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            ) or bool(public and host == public)

        def authenticated(self):
            try:
                cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
                value = cookie.get(cookie_name)
                return bool(value and hmac.compare_digest(value.value, key))
            except (http.cookies.CookieError, TypeError):
                return False

        def process(self):
            if not self.host_ok():
                return self.reply(403, b"Invalid reading host")
            parsed = urllib.parse.urlsplit(self.path)
            if self.command == "GET" and parsed.path.startswith("/enter/"):
                candidate = parsed.path[len("/enter/") :]
                if not hmac.compare_digest(candidate, key):
                    return self.reply(403, "入口无效，请使用微信里最新的个人阅读入口。".encode())
                target = safe_target(urllib.parse.parse_qs(parsed.query).get("to", ["/"])[0])
                local = (
                    self.headers.get("Host", "").startswith(("127.0.0.1:", "localhost:"))
                    and self.headers.get("X-Forwarded-Proto") != "https"
                )
                cookie = (
                    cookie_name
                    + "="
                    + key
                    + "; HttpOnly; SameSite=Lax; Path=/"
                    + ("" if local else "; Secure")
                )
                return self.reply(303, headers={"Set-Cookie": cookie, "Location": target})
            if not self.authenticated():
                return self.reply(401, "请从微信里的个人“今日文献”链接进入。".encode())
            if not permitted(parsed.path, self.command):
                return self.reply(403, b"Reading operation only")
            data = None
            if self.command == "POST":
                origin = self.headers.get("Origin", "")
                host = self.headers.get("Host", "")
                public = (store.meta("reader_site") or {}).get("url", "").rstrip("/")
                allowed = {
                    f"http://127.0.0.1:{self.server.server_port}",
                    f"http://localhost:{self.server.server_port}",
                    public,
                }
                if (
                    not origin
                    or origin not in allowed
                    or self.headers.get("Content-Type") != "application/json"
                ):
                    return self.reply(403, b"Use the reading page controls")
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return self.reply(400, b"Invalid request")
                if not 0 < size <= 16000:
                    return self.reply(400, b"Invalid request")
                data = self.rfile.read(size)
            headers = {
                "Host": urllib.parse.urlsplit(backend).netloc,
                "User-Agent": "LiteratureReaderGateway/2",
                "Accept": self.headers.get("Accept", "*/*"),
            }
            if instance:
                headers["X-Literature-Instance"] = instance
            if data is not None:
                headers.update({"Content-Type": "application/json", "Origin": backend})
            request = urllib.request.Request(
                backend + self.path, data=data, headers=headers, method=self.command
            )
            try:
                with upstream.open(request, timeout=25) as response:
                    static = parsed.path.startswith(("/_next/static/", "/assets/")) and re.search(
                        r"[-.][A-Za-z0-9_-]{7,}\.(?:js|css|woff2?)$", parsed.path
                    )
                    cache = (
                        "private, max-age=604800, immutable"
                        if response.status == 200 and static
                        else "no-store"
                    )
                    body = response.read(10000000)
                    self.reply(
                        response.status,
                        body,
                        response.headers.get("Content-Type", "application/octet-stream"),
                        cache=cache,
                    )
            except urllib.error.HTTPError as exc:
                self.reply(exc.code, exc.read(16000), exc.headers.get("Content-Type", "text/plain"))
            except Exception:
                self.reply(503, "阅读服务正在恢复，请稍后刷新。".encode())

        do_GET = process
        do_POST = process

    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True

    server = Server(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="reader-gateway").start()
    return server


class Manager:
    def __init__(self, store):
        self.store = store
        self.server = None
        self.api_proxy = None
        self.worker = None
        self.tunnel = None
        self.public = ""
        self.registered = False
        self.worker_log = None
        self.last_probe = 0
        self.last_start = 0
        self.last_tunnel_start = 0
        self.run_id = uuid.uuid4().hex

    def start(self):
        self.store.put(
            "reader_gateway", {"status": "starting", "run_id": self.run_id, "time": time.time()}
        )
        self.server = start_gateway(self.store)
        if (self.store.meta("reader_runtime") or {}).get("public"):
            self.api_proxy = start_api_proxy(self.store, RELAY_PORT)
        old = self.store.meta("reader_site") or {}
        self.store.put(
            "reader_site",
            {
                "id": old.get("id", ""),
                "mode": "local",
                "url": f"http://127.0.0.1:{PORT}",
                "worker_origin": BACKEND,
                "enabled": True,
            },
        )
        self.store.put("reader_digest_synced", "")

    def stop_process(self, process):
        if process and process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                process.terminate()

    def close(self):
        self.stop_process(self.tunnel)
        self.stop_process(self.worker)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.api_proxy:
            self.api_proxy.shutdown()
            self.api_proxy.server_close()
        if self.worker_log:
            self.worker_log.close()

    def tunnel_output(self, process):
        for line in process.stdout:
            if self.tunnel is not process:
                return
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if match:
                self.public = match[0]
            if "ERR" in line or "failed to request quick Tunnel" in line:
                self.store.put(
                    "reader_gateway",
                    {"status": "reconnecting", "message": line.strip()[:400], "time": time.time()},
                )
            if "Registered tunnel connection" in line:
                self.registered = True
                address = re.search(r"\bip=([0-9.]+)\b", line)
                if address:
                    self.store.put(
                        "reader_edge_good",
                        {"time": time.time(), "addresses": [address[1] + ":7844"]},
                    )
            if self.public and self.registered:
                old = self.store.meta("reader_site") or {}
                value = {
                    "id": old.get("id", ""),
                    "mode": "local",
                    "url": self.public,
                    "worker_origin": BACKEND,
                    "enabled": True,
                }
                if old != value:
                    self.store.put("reader_site", value)
                    self.store.put("reader_digest_synced", "")
                self.store.put(
                    "reader_gateway",
                    {
                        "status": "connected",
                        "time": time.time(),
                        "url": self.public,
                        "run_id": self.run_id,
                    },
                )

    def tick(self):
        config = self.store.meta("reader_runtime") or {}
        if not config.get("enabled"):
            return
        current = time.time()
        if current - self.last_probe < 10:
            return
        self.last_probe = current
        try:
            request = urllib.request.Request(
                BACKEND + "/api/health",
                headers={"X-Literature-Instance": config.get("instance_id", "")},
            )
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                request, timeout=2
            ) as response:
                ready = response.status == 200 and json.load(response).get(
                    "instance"
                ) == config.get("instance_id")
        except Exception:
            ready = False
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        if not ready:
            if (
                not self.worker or self.worker.poll() is not None
            ) and current - self.last_start > 30:
                self.last_start = current
                root = Path(config["project_root"])
                self.worker_log = open(self.store.home / "reader-worker.log", "w", encoding="utf-8")
                args = [
                    config["node"],
                    "--import",
                    (root / "scripts/runtime-env.mjs").as_uri(),
                    str(root / "node_modules/wrangler/bin/wrangler.js"),
                    "dev",
                    "--config",
                    str(root / "dist/server/wrangler.json"),
                    "--local",
                    "--persist-to",
                    str(config.get("persist_path", self.store.home / "reader-state")),
                    "--ip",
                    "127.0.0.1",
                    "--port",
                    str(urllib.parse.urlsplit(BACKEND).port),
                    "--inspector-port",
                    "0",
                    "--var",
                    "LC_INSTANCE_ID:" + config["instance_id"],
                ]
                self.worker = subprocess.Popen(
                    args,
                    cwd=root,
                    stdout=self.worker_log,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                )
            return
        if not config.get("public"):
            self.store.put("reader_gateway", {"status": "local", "time": current})
            return
        if (
            not self.tunnel or self.tunnel.poll() is not None
        ) and current - self.last_tunnel_start > 60:
            binary = Path(config["cloudflared"])
            if not binary.is_file():
                self.store.put(
                    "reader_gateway", {"status": "error", "message": "连接组件尚未准备好"}
                )
                return
            self.last_tunnel_start = current
            self.public = ""
            self.registered = False
            env = os.environ.copy()
            # Windows' configured resolver currently fails SRV lookups. Resolve
            # the real provider endpoints for this child only; keep TLS checks
            # and all system DNS/hosts settings unchanged.
            try:
                env["TUNNEL_EDGE"] = resolve_edges(self.store)
            except (RuntimeError, subprocess.SubprocessError):
                self.store.put(
                    "reader_gateway",
                    {
                        "status": "error",
                        "message": "通道域名暂未解析，稍后自动重连",
                        "time": current,
                    },
                )
                return
            self.tunnel = subprocess.Popen(
                [
                    str(binary),
                    "tunnel",
                    "--url",
                    f"http://127.0.0.1:{PORT}",
                    "--quick-service",
                    f"http://127.0.0.1:{RELAY_PORT}",
                    "--no-autoupdate",
                    "--protocol",
                    "http2",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
                env=env,
            )
            threading.Thread(
                target=self.tunnel_output, args=(self.tunnel,), daemon=True, name="reader-tunnel"
            ).start()


def resolve_edges(store=None):
    script = r"""$ErrorActionPreference='Stop'
function Resolve-ProviderName($domain){
 foreach($resolver in @('119.29.29.29','223.5.5.5','8.8.8.8')){
  try {
   $records=@(Resolve-DnsName $domain -Type A -Server $resolver -DnsOnly -NoHostsFile -QuickTimeout |
    Where-Object { $_.Name -eq $domain -and $_.IPAddress -match '^\d+\.\d+\.\d+\.\d+$' })
   if($records.Count){return $records}
  } catch {}
 }
 return @()
}
Resolve-ProviderName 'api.trycloudflare.com' | Select-Object -First 2 -ExpandProperty IPAddress | ForEach-Object { 'api:'+$_ }
@('region1.v2.argotunnel.com','region2.v2.argotunnel.com') | ForEach-Object {
 Resolve-ProviderName $_ | Select-Object -First 2 -ExpandProperty IPAddress
}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=18,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    import ipaddress

    values = []
    api = []
    for line in result.stdout.splitlines():
        try:
            is_api = line.startswith("api:")
            address = ipaddress.ip_address(line[4:].strip() if is_api else line.strip())
            if address.version == 4 and address.is_global:
                (api if is_api else values).append(
                    str(address) if is_api else str(address) + ":7844"
                )
        except ValueError:
            pass
    if api and store:
        store.put("reader_api_dns", {"time": time.time(), "addresses": api})
    cached = (store.meta("reader_edge_good") or {}) if store else {}
    if time.time() - cached.get("time", 0) < 86400:
        values.extend(cached.get("addresses", []))
    if result.returncode or not values:
        raise RuntimeError("通道域名暂未解析")

    def reachable(value):
        try:
            host, port = value.rsplit(":", 1)
            if int(port) != 7844 or not ipaddress.ip_address(host).is_global:
                return None
            with socket.create_connection((host, int(port)), timeout=2):
                return value
        except (ValueError, OSError):
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        connected = [x for x in pool.map(reachable, dict.fromkeys(values)) if x]
    if not connected:
        raise RuntimeError("通道连接暂不可用")
    return ",".join(connected)


def start_api_proxy(store, port=18745):
    """Loopback quick-service relay, verifying the provider's TLS hostname.

    cloudflared's quick-service HTTP transport ignores HTTPS_PROXY. Its official
    quick-service override points here; only empty POST /tunnel is accepted and
    forwarded to the genuine API. Never log provisioning credentials.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_CONNECT(self):
            self.send_error(403)

        def do_POST(self):
            if (
                self.path != "/tunnel"
                or self.headers.get("Origin")
                or self.headers.get("Host") != "127.0.0.1:" + str(self.server.server_port)
                or self.headers.get("Transfer-Encoding")
                or self.headers.get("Content-Length", "0") != "0"
            ):
                self.send_error(403)
                return
            cached = store.meta("reader_api_dns") or {}
            if time.time() - cached.get("time", 0) > 86400:
                self.send_error(503)
                return
            for address in cached.get("addresses", []):
                import ipaddress

                client = None
                try:
                    if not ipaddress.ip_address(address).is_global:
                        continue
                    client = http.client.HTTPSConnection("api.trycloudflare.com", timeout=8)
                    client.sock = ssl.create_default_context().wrap_socket(
                        socket.create_connection((address, 443), timeout=8),
                        server_hostname="api.trycloudflare.com",
                    )
                    client.request(
                        "POST",
                        "/tunnel",
                        body=b"",
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": "cloudflared/2026.10.5",
                        },
                    )
                    response = client.getresponse()
                    body = response.read(1048577)
                    if len(body) > 1048576:
                        raise ValueError("oversized provisioning response")
                    self.send_response(response.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                except (ValueError, OSError):
                    continue
                finally:
                    if client:
                        client.close()
            self.send_error(503)

    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True

    server = Server(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="reader-api-connector").start()
    return server


def notify_entry(store, client):
    site = store.meta("reader_site") or {}
    if (
        not site.get("enabled")
        or site.get("mode") != "local"
        or not site.get("url", "").startswith("https://")
        or not urllib.parse.urlsplit(site["url"]).hostname.endswith(".trycloudflare.com")
    ):
        return
    from reader_portal import sync_portal

    fixed = sync_portal(store, client)
    if not fixed:
        return
    key = "reader-entry-fixed:" + (store.meta("reader_portal") or {})["docCode"]
    content = (
        "今日文献 · 固定入口\n"
        + fixed
        + "\n\n请保存这个链接。以后更新仍用同一个入口，打开后点“进入今日五篇”，无需重新扫码。每篇下方可点粗读、精读、中文翻译、原文；下一行是喜欢、无感。"
    )
    with store.db() as db:
        existing = db.execute("SELECT status FROM delivery WHERE key=?", (key,)).fetchone()
    if existing:
        if existing["status"] == "failed":
            client.send(key + ":wechat", "文献助手 · 今日文献", content, "wechat")
        return
    from companion import ProviderRejected

    try:
        result = client.send(key, "文献助手 · 今日文献", content, "clawbot", template="txt")
    except ProviderRejected:
        result = {"status": "failed"}
    if result["status"] == "failed":
        client.send(key + ":wechat", "文献助手 · 今日文献", content, "wechat")
