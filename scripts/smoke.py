"""Exercise an installed demo without models, account secrets or WeChat."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
from pathlib import Path
import socket
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import manage, runtime


def check(home: Path):
    config = manage.installation(home)
    if not config.get("demo"):
        raise RuntimeError("验证只允许演示实例，不操作正式数据。")
    store = runtime.store(home)
    runtime_config = store.meta("reader_runtime")
    instance = runtime_config["instance_id"]
    base = "http://127.0.0.1:"
    backend = base + str(config["ports"]["worker"])
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        direct.open(backend + "/api/health", timeout=10)
        raise AssertionError("worker accepted an unidentified caller")
    except urllib.error.HTTPError as error:
        assert error.code == 409
    request = urllib.request.Request(
        backend + "/api/health", headers={"X-Literature-Instance": instance}
    )
    assert json.load(direct.open(request, timeout=10))["instance"] == instance
    from reader_gateway import secret

    gateway = base + str(config["ports"]["reader"])
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )
    opener.open(gateway + "/enter/" + secret(store), timeout=20).read()
    digest = json.load(opener.open(gateway + "/api/today", timeout=20))["digest"]
    assert len(digest["cards"]) == 5
    assert "演示" in json.dumps(digest, ensure_ascii=False)
    history = opener.open(gateway + "/api/batches?kind=topic", timeout=20).read()
    manage.stop(home, 120)
    # An unrelated process takes the old worker port. Restart must choose a
    # complete new port group and keep the same private instance and data.
    held = socket.socket()
    held.bind(("127.0.0.1", config["ports"]["worker"]))
    held.listen()
    try:
        manage.start(home, True)
        updated = manage.installation(home)
        assert updated["ports"]["worker"] != config["ports"]["worker"]
    finally:
        held.close()
    gateway = base + str(updated["ports"]["reader"])
    opener.open(gateway + "/enter/" + secret(store), timeout=20).read()
    after = json.load(opener.open(gateway + "/api/today", timeout=20))["digest"]
    assert after == digest
    assert opener.open(gateway + "/api/batches?kind=topic", timeout=20).read() == history
    assert store.meta("reader_runtime")["instance_id"] == instance
    assert not store.profile()["enabled"]
    assert not store.profile()["wechat"]["enabled"]
    print(
        "PASS: five demo cards, instance isolation, occupied-port recovery, restart persistence; no AI or WeChat"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        check(arguments.home.resolve())
    finally:
        manage.stop(arguments.home.resolve(), 120)
