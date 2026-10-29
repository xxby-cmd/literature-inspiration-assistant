"""A provider-scoped DNS fallback; no system networking settings change."""

import http.client
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import time
import urllib.parse

HOST = "www.pushplus.plus"
_cache = (0, [])


class NotSubmitted(RuntimeError):
    """The connection failed before an API request could be submitted."""


def addresses():
    global _cache
    if _cache[0] > time.monotonic():
        return _cache[1]
    result = subprocess.run(
        ["nslookup", "-timeout=2", "-retry=1", HOST, "223.5.5.5"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=7,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    # Only addresses following the exact queried name, never the resolver IP.
    answer = result.stdout.partition(HOST)[2]
    values = []
    for value in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", answer):
        try:
            if ipaddress.ip_address(value).is_global:
                values.append(value)
        except ValueError:
            pass
    if not values:
        raise OSError("provider DNS unavailable")
    _cache = (time.monotonic() + 300, list(dict.fromkeys(values)))
    return _cache[1]


def resolved_request(request):
    parsed = urllib.parse.urlsplit(request.full_url)
    if parsed.scheme != "https" or parsed.hostname != HOST or parsed.port not in (None, 443):
        raise ValueError("provider origin mismatch")
    client = http.client.HTTPSConnection(HOST, timeout=25)
    submitted = False
    try:
        client.sock = ssl.create_default_context().wrap_socket(
            socket.create_connection((addresses()[0], 443), timeout=10), server_hostname=HOST
        )
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        submitted = True
        client.request(
            request.get_method(), target, body=request.data, headers=dict(request.header_items())
        )
        response = client.getresponse()
        body = response.read(3000001)
        if len(body) > 3000000:
            raise ValueError("provider response too large")
        return json.loads(body)
    except (OSError, subprocess.SubprocessError) as exc:
        if not submitted:
            raise NotSubmitted("连接未建立，请求尚未发送") from None
        raise
    finally:
        client.close()
