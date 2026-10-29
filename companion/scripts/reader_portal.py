"""A stable, login-required PushPlus document points to the current reader."""

import hashlib
import html
import re
import urllib.parse
from companion import now

TITLE = "文献启发助手 · 固定入口"


def valid_url(url):
    parsed = urllib.parse.urlsplit(url or "")
    return (
        parsed.scheme == "https"
        and parsed.netloc == "www.pushplus.plus"
        and bool(re.fullmatch(r"/push/doc/[A-Za-z0-9]{8,64}", parsed.path))
        and not parsed.query
        and not parsed.fragment
    )


def fixed_url(store):
    portal = store.meta("reader_portal") or {}
    return portal["shareUrl"] if portal.get("ready") and valid_url(portal.get("shareUrl")) else ""


def sync_portal(store, client):
    from reader_gateway import direct_entry_url

    target = direct_entry_url(store)
    if not target:
        return ""
    portal = store.meta("reader_portal") or {}
    if not portal.get("docCode"):
        listed = client.document(
            "list", {"current": 1, "pageSize": 50, "params": {"keyword": TITLE}}
        )
        candidates = [r for r in listed.get("list", []) if r.get("title") == TITLE]
        if len(candidates) > 1:
            raise RuntimeError("固定入口文档不唯一，需要核对，未重复创建")
        if candidates:
            created = candidates[0]
        else:
            if store.meta("reader_portal_creating"):
                raise RuntimeError("固定入口创建结果待核对，未重复创建")
            store.put("reader_portal_creating", True)
            try:
                created = client.document("create", {"title": TITLE})
            except Exception:
                # A lost create response is recovered by the title search above.
                raise
        if not re.fullmatch(r"[A-Za-z0-9]{8,64}", created.get("docCode", "")) or not valid_url(
            created.get("shareUrl")
        ):
            raise RuntimeError("固定入口返回格式不符")
        portal = {k: created[k] for k in ("docCode", "shareUrl")}
        portal["ready"] = False
        store.put("reader_portal", portal)
        store.put("reader_portal_creating", False)
    signature = hashlib.sha256(("reader-portal-v1:" + target).encode()).hexdigest()
    if portal.get("signature") == signature and portal.get("ready"):
        return portal["shareUrl"]
    code = portal["docCode"]
    body = (
        "<h1>今日文献</h1><p>三篇贴近兴趣，两篇打开眼界。</p>"
        '<h2><a href="'
        + html.escape(target, quote=True)
        + '" target="_blank" rel="noopener noreferrer">进入今日五篇 →</a></h2>'
        "<p>请保存当前这一个固定入口。以后助手更新后，仍打开本页，再点上面的蓝色链接，无需重新扫码。</p>"
        "<p>可继续粗读、精读、中文翻译、逐层通俗解释和实践。电脑需保持开机联网。</p>"
        "<p>入口更新：" + now().strftime("%Y-%m-%d %H:%M") + "（北京时间）</p>"
    )
    client.document("saveContent", {"docCode": code, "content": body})
    client.document("publish?" + urllib.parse.urlencode({"docCode": code}), {})
    # PushPlus supports owner viewing while sharing is closed. Keep this
    # personal gateway owner-only; never require publishing it to other users.
    verified = client.document("content?" + urllib.parse.urlencode({"docCode": code}))
    if (
        not verified.get("published")
        or verified.get("publishDirty")
        or verified.get("sharePerm") != 0
        or verified.get("shareLogin") != 1
        or html.escape(target, quote=True) not in verified.get("content", "")
    ):
        raise RuntimeError("固定入口尚未实际发布或登录保护不符")
    portal.update(ready=True, signature=signature, updated=now().isoformat(), login_required=True)
    store.put("reader_portal", portal)
    return portal["shareUrl"]
