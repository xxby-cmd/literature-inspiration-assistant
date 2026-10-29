"""Private loopback reader synchronization; no model API or hosted account."""

import hashlib
import json
import re
import time
import urllib.request
import urllib.parse
import urllib.error


class Client:
    def __init__(self, store):
        site = store.meta("reader_site") or {}
        from reader_gateway import BACKEND

        if site.get("mode") != "local" or site.get("worker_origin") != BACKEND:
            raise ValueError("本机阅读服务地址无效")
        self.url = BACKEND
        self.instance = (store.meta("reader_runtime") or {}).get("instance_id", "")
        if not re.fullmatch(r"[a-f0-9]{32}", self.instance):
            raise ValueError("阅读器实例尚未配置")

    def post(self, path, data):
        if path not in ("digest", "prepare", "claim", "heartbeat", "result"):
            raise ValueError("阅读同步路径无效")
        headers = {"Content-Type": "application/json", "User-Agent": "LiteratureCompanion/2"}
        headers["X-Literature-Instance"] = self.instance
        req = urllib.request.Request(
            self.url + "/api/sync/" + path,
            data=json.dumps(data, ensure_ascii=False).encode(),
            headers=headers,
        )

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open(
                req, timeout=25
            ) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError("私人阅读页访问未通过，HTTP " + str(exc.code)) from None
        except (ValueError, json.JSONDecodeError):
            raise RuntimeError("私人阅读页未返回数据，需核对站点访问身份") from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("私人阅读页暂未连通；已完成内容保留待同步") from None
        if result.get("error"):
            raise RuntimeError("阅读同步请求未完成")
        return result


def cleanup(store, current=None):
    current = time.time() if current is None else current
    with store.db() as db:
        db.execute(
            "UPDATE reader_requests SET result='',status='expired' WHERE expires IS NOT NULL AND expires<=? AND result!=''",
            (current,),
        )


def complete(store, job_id, result):
    with store.db() as db:
        row = db.execute("SELECT payload FROM reader_requests WHERE id=?", (job_id,)).fetchone()
        payload = json.loads(row["payload"])
        data = {"job_id": job_id, "node_id": payload["node_id"], "ok": True, **result}
        db.execute(
            "UPDATE reader_requests SET result=?,expires=?,status='complete',uploaded=0 WHERE id=?",
            (json.dumps(data, ensure_ascii=False), result.get("expires_at"), job_id),
        )


def failed(store, job_id):
    with store.db() as db:
        row = db.execute("SELECT payload FROM reader_requests WHERE id=?", (job_id,)).fetchone()
        if not row:
            return
        payload = json.loads(row["payload"])
        data = {
            "job_id": job_id,
            "node_id": payload["node_id"],
            "ok": False,
            "title": "本次阅读尚未完成",
            "body": "",
            "scope": "",
            "error": "这次解读未完成，已保留论文定位。请稍后在阅读页重新尝试。",
        }
        db.execute(
            "UPDATE reader_requests SET result=?,status='complete',uploaded=0 WHERE id=?",
            (json.dumps(data, ensure_ascii=False), job_id),
        )


def sync(store, allow_claim=True, client=None):
    site = store.meta("reader_site")
    if not site or not site.get("enabled"):
        return
    cleanup(store)
    client = client or Client(store)
    from companion import now

    digest = store.meta("digest:" + now().date().isoformat())
    if digest:
        signature = hashlib.sha256(json.dumps(digest, sort_keys=True).encode()).hexdigest()
        if store.meta("reader_digest_synced") != signature:
            client.post("digest", digest)
            store.put("reader_digest_synced", signature)
        if (
            store.profile().get("reading", {}).get("prepare_coarse")
            and store.meta("reader_coarse_prepared") != signature
        ):
            prepared = client.post("prepare", {"date": digest["date"]})
            if prepared.get("complete") is True:
                store.put("reader_coarse_prepared", signature)
                store.put(
                    "coarse_preparation",
                    {
                        "date": digest["date"],
                        "nodes": prepared.get("nodes", []),
                        "time": now().isoformat(),
                    },
                )
    with store.db() as db:
        rows = db.execute(
            "SELECT * FROM reader_requests WHERE status='complete' AND uploaded=0 ORDER BY rowid LIMIT 10"
        ).fetchall()
        expired = db.execute(
            "SELECT id,payload FROM reader_requests WHERE status='expired' AND uploaded=0 LIMIT 10"
        ).fetchall()
    for row in expired:
        payload = json.loads(row["payload"])
        client.post(
            "result",
            {
                "job_id": row["id"],
                "node_id": payload["node_id"],
                "ok": False,
                "title": "临时译文已到期",
                "body": "",
                "scope": "",
                "error": "这份临时译文已超过七天，请重新生成。",
            },
        )
        with store.db() as db:
            db.execute("UPDATE reader_requests SET uploaded=1 WHERE id=?", (row["id"],))
    for row in rows:
        data = json.loads(row["result"])
        client.post("result", data)
        with store.db() as db:
            db.execute("UPDATE reader_requests SET uploaded=1 WHERE id=?", (row["id"],))
    with store.db() as db:
        active = [
            r[0]
            for r in db.execute(
                "SELECT r.id FROM reader_requests r JOIN inbox i ON i.id=r.inbox_id WHERE i.status IN ('queued','running') LIMIT 20"
            )
        ]
    client.post("heartbeat", {"ids": active})
    if allow_claim:
        with store.db() as db:
            pending = db.execute(
                "SELECT COUNT(*) FROM inbox WHERE status IN ('queued','running')"
            ).fetchone()[0]
        if pending < 8:
            job = client.post("claim", {}).get("job")
            if job:
                if not re.fullmatch(r"[a-f0-9]{32}-[1-9]\d*", job["id"]):
                    raise ValueError("阅读任务编号无效")
                payload = job["payload"]
                from reading_engine import validate_payload

                validate_payload(store, payload)
                with store.db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    existing = db.execute(
                        "SELECT id FROM reader_requests WHERE id=?", (job["id"],)
                    ).fetchone()
                    if not existing:
                        cursor = db.execute(
                            "INSERT INTO inbox(text,status,answer,error,source,reader_job) VALUES (?,?,?,?,?,?)",
                            ("阅读页：" + payload["kind"], "queued", "", "", "panel", job["id"]),
                        )
                        db.execute(
                            "INSERT INTO reader_requests(id,inbox_id,payload,result,status,uploaded) VALUES (?,?,?,?,?,?)",
                            (
                                job["id"],
                                cursor.lastrowid,
                                json.dumps(payload, ensure_ascii=False),
                                "",
                                "queued",
                                0,
                            ),
                        )
                    else:
                        db.execute(
                            "UPDATE reader_requests SET uploaded=0 WHERE id=? AND status='complete'",
                            (job["id"],),
                        )
    store.put("reader_sync", {"time": now().isoformat(), "status": "ok"})
