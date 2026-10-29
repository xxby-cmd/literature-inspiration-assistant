"""Loopback-only touch panel. Uses the connector's durable queue and replies."""

import datetime as dt
import html
import http.server
import json
import os
from pathlib import Path
import re
import threading
import urllib.parse


ACTIONS = {
    "today": "今日文献",
    "preferences": "我的偏好",
    "coarse": "粗读",
    "deep": "精读",
    "like": "喜欢",
    "dislike": "不感兴趣",
    "hard": "太难",
    "read": "已读",
    "practice": "想实践",
    "topic": "查课题",
}
PAPER_ACTIONS = {"coarse", "deep", "like", "dislike", "hard", "read", "practice"}


def snapshot(store):
    date = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    digest = store.meta("digest:" + date)
    # Before the morning edition is ready, the latest saved set remains readable.
    if not digest:
        with store.db() as db:
            row = db.execute(
                "SELECT value FROM meta WHERE key LIKE 'digest:%' ORDER BY key DESC LIMIT 1"
            ).fetchone()
        digest = json.loads(row[0]) if row else None
    return digest


def enqueue(store, data):
    action = data.get("action")
    request_id = data.get("request_id", "")
    if (
        action not in ACTIONS
        or not isinstance(request_id, str)
        or not re.fullmatch("[a-f0-9]{32}", request_id)
    ):
        raise ValueError("操作无效，请刷新后重试。")
    command = ACTIONS[action]
    if action in PAPER_ACTIONS:
        date, paper = data.get("date"), data.get("paper")
        if (
            not isinstance(date, str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
            or type(paper) is not int
            or not 1 <= paper <= 5
        ):
            raise ValueError("请先选择论文。")
        digest = store.meta("digest:" + date)
        if not digest or len(digest.get("cards", [])) != 5:
            raise ValueError("这组论文暂不可用，请刷新后重试。")
        card = digest["cards"][paper - 1]
        target = card["title_original"] + "｜" + card["field"] + "｜" + card["source_url"]
        command += "：" + target if action in ("coarse", "deep") else " " + target
        if action in ("coarse", "deep"):
            command += "\n这是" + date + "日报第" + str(paper) + "篇，请按以上原文链接定位。"
    elif action == "topic":
        topic = data.get("topic", "")
        if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 2000:
            raise ValueError("请填入你想研究的问题（最多2000字）。")
        command += "：" + topic.strip()
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT p.inbox_id,i.text FROM panel_requests p JOIN inbox i ON i.id=p.inbox_id WHERE p.id=?",
            (request_id,),
        ).fetchone()
        if existing:
            if existing["text"] != command:
                raise ValueError("操作编号冲突，请重新打开面板。")
            return {"id": request_id, "status": "queued"}
        pending = db.execute(
            "SELECT COUNT(*) FROM inbox WHERE source='panel' AND status IN ('queued','running')"
        ).fetchone()[0]
        if pending >= 8:
            raise ValueError("已有多项任务等待处理，请等完成后再选。")
        cursor = db.execute(
            "INSERT INTO inbox(text,status,answer,error,source) VALUES (?,?,?,?,?)",
            (command, "queued", "", "", "panel"),
        )
        db.execute("INSERT INTO panel_requests VALUES (?,?)", (request_id, cursor.lastrowid))
    return {"id": request_id, "status": "queued"}


def result(store, request_id):
    if not re.fullmatch("[a-f0-9]{32}", request_id):
        raise ValueError("任务编号无效。")
    with store.db() as db:
        row = db.execute(
            "SELECT i.status,i.answer FROM panel_requests p JOIN inbox i ON i.id=p.inbox_id WHERE p.id=?",
            (request_id,),
        ).fetchone()
    if not row:
        raise ValueError("没有找到这项任务。")
    return dict(row)


def page(store):
    digest = snapshot(store)
    cards = []
    if digest:
        for i, card in enumerate(digest["cards"], 1):
            group = "贴近兴趣" if card["group"] == "preference" else "跨领域探索"
            url = card["source_url"]
            if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
                url = "#"
            cards.append(
                '<label class="paper"><input type="radio" name="paper" value="' + str(i) + '">'
                "<span><small>"
                + str(i)
                + " / "
                + group
                + "</small><strong>"
                + html.escape(card["title_cn"])
                + "</strong>"
                '<span class="summary">'
                + html.escape(
                    re.sub(r"\[[^\]]+\]\(https?://[^\s)]+\)", "", card["plain_summary"]).strip()
                )
                + "</span>"
                '<a href="'
                + html.escape(url, quote=True)
                + '" target="_blank" rel="noopener noreferrer">查看原文 ↗</a></span></label>'
            )
    template = (Path(__file__).resolve().parents[1] / "references/button-panel.html").read_text(
        encoding="utf-8"
    )
    return (
        template.replace(
            "<!--CARDS-->",
            "\n".join(cards) or "<p>今天的五篇尚未准备好，可以先点“今日文献”准备。</p>",
        )
        .replace("<!--DATE-->", html.escape(digest["date"]) if digest else "")
        .replace("<!--ENGLISH-->", html.escape(digest.get("english", "")) if digest else "")
        .replace("<!--PRACTICE-->", html.escape(digest.get("practice", "")) if digest else "")
    )


def start_panel(store, port=None):
    port = int(os.environ.get("LITERATURE_PANEL_PORT", "18741")) if port is None else port

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def trusted(self):
            return self.headers.get("Host") in (
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            )

        def reply(self, status, value, kind="application/json; charset=utf-8"):
            body = (
                value.encode()
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.trusted():
                return self.reply(403, {"error": "仅限本机入口。"})
            try:
                if self.path == "/":
                    if (store.meta("reader_runtime") or {}).get("enabled"):
                        from reader_gateway import local_entry_url

                        self.send_response(303)
                        self.send_header("Location", local_entry_url(store))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        return
                    self.reply(200, page(store), "text/html; charset=utf-8")
                elif self.path.startswith("/result/"):
                    self.reply(200, result(store, self.path[len("/result/") :]))
                else:
                    self.reply(404, {"error": "没有这个入口。"})
            except (ValueError, KeyError):
                self.reply(400, {"error": "内容暂不可用，请刷新后重试。"})

        def do_POST(self):
            expected = "http://" + self.headers.get("Host", "")
            if (
                not self.trusted()
                or self.headers.get("Origin") != expected
                or self.headers.get("X-Panel-Request") != "1"
            ):
                return self.reply(403, {"error": "请从文献面板选择操作。"})
            if self.path != "/action":
                return self.reply(404, {"error": "没有这个入口。"})
            if not store.profile()["wechat"]["enabled"]:
                return self.reply(503, {"error": "微信助手尚未启用，请先完成接入。"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16000 or self.headers.get("Content-Type") != "application/json":
                    raise ValueError("请求格式无效。")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("请求格式无效。")
                self.reply(200, enqueue(store, data))
            except (ValueError, TypeError) as exc:
                self.reply(
                    400,
                    {
                        "error": str(exc)
                        if not isinstance(exc, json.JSONDecodeError)
                        else "请求格式无效。"
                    },
                )

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="literature-buttons").start()
    store.put(
        "panel",
        {"url": f"http://127.0.0.1:{server.server_port}/", "scope": "loopback", "running": True},
    )
    return server
