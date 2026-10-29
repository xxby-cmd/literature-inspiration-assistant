"""PushPlus member form -> owner receipt -> the existing durable action queue.

No public local server or third-party relay. The callback stays at PushPlus.
The account's scoped message rule stores the original JSON using the txt template.
"""

import hashlib
from html.parser import HTMLParser
import json
import re
import urllib.parse
import urllib.request

from button_panel import ACTIONS, enqueue

BRIDGE_TITLE = "文献助手 · 已收到选择"
MARKER = "文献助手表单桥接_20261029"
LABELS = {v: k for k, v in ACTIONS.items()}
LABELS["查新课题"] = "topic"


class MessageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif dict(attrs).get("id") == "messageContent":
                self.depth = 1
        elif tag == "br" and self.depth:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "div" and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


def parse_receipt(document):
    parser = MessageText()
    parser.feed(document)
    text = "".join(parser.parts).strip()
    # A human-readable acknowledgment may precede the stored command envelope.
    start = text.find('{"bridge":')
    if start < 0:
        raise ValueError("没有找到回调信封")
    data = json.loads(text[start:])
    if (
        not isinstance(data, dict)
        or data.get("bridge") != "literature-form-v1"
        or not isinstance(data.get("payload"), dict)
    ):
        raise ValueError("回调信封格式不符")
    return data["payload"]


def action_from_event(event, form):
    if event.get("event") == "form.webhook.test":
        return None
    if (
        event.get("event") != "form.submit"
        or str(event.get("formId")) != str(form["id"])
        or event.get("formCode") != form["formCode"]
    ):
        raise ValueError("回调不是已绑定的助手表单")
    if event.get("updated"):
        return None  # Editing a prior response must not repeat an earlier action.
    response_id = event.get("responseId")
    if isinstance(response_id, bool) or not re.fullmatch(r"[1-9]\d{0,19}", str(response_id)):
        raise ValueError("答卷编号无效")
    answers = event.get("answerMap")
    if not isinstance(answers, dict):
        raise ValueError("没有找到选择内容")
    action = LABELS.get(answers.get("action"))
    if not action:
        raise ValueError("请选择一个助手操作")
    request_id = hashlib.sha256(
        ("push-form:" + str(form["id"]) + ":" + str(response_id)).encode()
    ).hexdigest()[:32]
    data = {"action": action, "request_id": request_id, "topic": answers.get("topic", "")}
    if action not in ("today", "preferences", "topic"):
        paper = answers.get("paper", "")
        match = (
            re.match(r"^(\d{4}-\d{2}-\d{2})｜([1-5])｜", paper) if isinstance(paper, str) else None
        )
        if not match:
            raise ValueError("这项操作需要论文，请选好论文后重新提交")
        data.update(date=match[1], paper=int(match[2]))
    return data


def design(store, digest):
    form = store.meta("push_form")
    bot = store.profile()["wechat"].get("form_reply_channel") == "clawbot"
    callback = (
        "https://www.pushplus.plus/send/"
        + store.credentials()["token"]
        + "?"
        + urllib.parse.urlencode(
            {
                "content": MARKER,
                "template": "txt",
                "channel": "wechat",
                "option": store.profile()["wechat"]["official_option"],
            }
        )
    )
    return {
        "id": form["id"],
        "title": "文献助手 · 点选入口",
        "description": "选择操作后点“提交”，助手自动开始。论文按日期定位；新课题才需要输入内容。每天三篇兴趣和两篇跨领域探索。",
        "items": [
            {
                "id": "action",
                "alias": "action",
                "type": "radio",
                "label": "你想做什么？",
                "required": True,
                "options": [
                    "今日文献",
                    "我的偏好",
                    "粗读",
                    "精读",
                    "喜欢",
                    "不感兴趣",
                    "太难",
                    "已读",
                    "想实践",
                    "查新课题",
                ],
                "allowOther": False,
            },
            {
                "id": "paper",
                "alias": "paper",
                "type": "select",
                "label": "选哪一篇论文？",
                "required": False,
                "description": "粗读、精读和论文反馈时请选择；今日文献、我的偏好和查新课题可跳过。",
                "options": [
                    digest["date"] + "｜" + str(i) + "｜" + c["title_cn"]
                    for i, c in enumerate(digest["cards"], 1)
                ],
            },
            {
                "id": "topic",
                "alias": "topic",
                "type": "textarea",
                "label": "新课题或研究问题（选填）",
                "required": False,
                "description": "只有选择“查新课题”时需要填写，也可用手机键盘的语音输入。",
                "placeholder": "直接描述你想研究的问题即可",
            },
        ],
        "theme": {"primaryColor": "#205b4a", "backgroundColor": "#f3f6f5"},
        "settings": {
            "oncePerUser": False,
            "allowAnonymous": False,
            "showQuestionNumber": False,
            "hideAd": True,
            "hideCopyright": True,
            "allowEdit": False,
            "thankText": (
                "选择已提交。助手会自动处理，优先回复到微信ClawBot；若该通道明确投递失败，使用“pushplus 推送加”备用回复。"
                if bot
                else "选择已提交。助手会自动处理，并通过“pushplus 推送加”回复。"
            )
            + "无需再发文字指令。电脑需保持开机。",
            "webhookEnabled": True,
            "webhookUrl": callback,
        },
    }


def sync_form(store, client, digest):
    form = store.meta("push_form")
    if not form or not form.get("enabled"):
        return
    signature = hashlib.sha256(
        json.dumps(
            {
                "version": 2,
                "digest": digest,
                "reply_channel": store.profile()["wechat"].get("form_reply_channel", "wechat"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if form.get("synced_digest") == signature:
        return
    client.form("save", design(store, digest))
    published = client.form("publish?id=" + str(form["id"]), {})
    verified = client.form("detail?id=" + str(form["id"]))
    if verified.get("settings", {}).get("webhookEnabled") is not True:
        raise ValueError("表单回调未实际启用，需核对会员权限")
    form.update({k: published[k] for k in ("formCode", "fillUrl", "status")})
    form.update(date=digest["date"], synced_digest=signature)
    store.put("push_form", form)


def menu_footer(store):
    reader = store.meta("reader_site")
    if reader and reader.get("enabled"):
        from reader_gateway import entry_url

        link = entry_url(store)
        if link:
            return "\n\n今日文献\n" + link
    form = store.meta("push_form")
    if not form or not form.get("enabled") or not form.get("fillUrl"):
        return ""
    # Never put a callback, token or AccessKey in the public filling link.
    return (
        "\n\n[点选操作：粗读、精读、反馈、查课题]("
        + form["fillUrl"]
        + ")\n选择后点“提交”即可，不用输入指令。论文列表日期："
        + form["date"]
        + "。"
    )


def receipt_document(short_code):
    if not re.fullmatch("[A-Za-z0-9]{8,64}", short_code):
        raise ValueError("回执编号格式不符")
    req = urllib.request.Request(
        "https://www.pushplus.plus/shortMessage/" + short_code,
        headers={"User-Agent": "LiteratureCompanion/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read(300001)
    if len(raw) > 300000:
        raise ValueError("回执超出读取范围")
    return raw.decode("utf-8")


def poll_forms(store, client):
    form = store.meta("push_form")
    if not form or not form.get("enabled"):
        return
    # Metadata only for unrelated messages; retrieve content only for this bridge.
    # Up to 500 recent receipts per poll; bounded catch-up prevents runaway calls.
    count = 0
    head = store.meta("form_poll_head")
    newest = None
    reached = False
    candidates = []
    for page in range(1, 11):
        listing = client.messages(page, 50)
        rows = listing.get("list", [])
        if page == 1 and rows:
            newest = rows[0].get("shortCode")
        for row in rows:
            if head and row.get("shortCode") == head:
                reached = True
                break
            candidates.append(row)
        if reached or page >= int(listing.get("pages") or 1):
            break
    for row in reversed(candidates):
        if row.get("title") != BRIDGE_TITLE:
            continue
        code = row.get("shortCode", "")
        with store.db() as db:
            seen = db.execute("SELECT 1 FROM form_receipts WHERE code=?", (code,)).fetchone()
        if seen:
            continue
        if code == store.meta("form_test_receipt"):
            status = "legacy_test_ignored"
        else:
            try:
                event = parse_receipt(receipt_document(code))
                data = action_from_event(event, form)
                if data:
                    enqueue(store, data)
                    count += 1
                    status = "queued"
                else:
                    status = "test_or_edit_ignored"
            except (ValueError, KeyError, TypeError):
                # Malformed data cannot reach a model. One stable error receipt.
                client.send(
                    "form-invalid:" + code,
                    "文献助手 · 请补选",
                    "这次选择未进入处理队列。粗读、精读或反馈请先选论文；查新课题请填写问题，然后重新提交。",
                    "wechat",
                )
                status = "invalid"
        with store.db() as db:
            db.execute("INSERT OR IGNORE INTO form_receipts VALUES (?,?)", (code, status))
    truncated = not reached and int(listing.get("pages") or 1) > 10
    if newest and not truncated:
        store.put("form_poll_head", newest)
    store.put(
        "form_poll", {"queued_this_poll": count, "status": "catchup_limit" if truncated else "ok"}
    )
