"""Optional owner-authorized WeChat onboarding; all secrets stay in DPAPI."""

import getpass
import json
from pathlib import Path
import re
import time
import urllib.request
import uuid
import webbrowser

from .runtime import store, ensure_cloudflared


def current_ip():
    try:
        with urllib.request.urlopen("https://myip.ipip.net", timeout=10) as response:
            text = response.read(1000).decode("utf-8")
        match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
        return match[0] if match else "请在网络服务商页面查看"
    except Exception:
        return "暂未取得，请按自己的网络出口填写"


def channel_ready(s, started):
    gateway = s.meta("reader_gateway") or {}
    site = s.meta("reader_site") or {}
    url = gateway.get("url", "")
    return (
        gateway.get("status") == "connected"
        and bool(gateway.get("run_id"))
        and gateway.get("time", 0) >= started
        and url.startswith("https://")
        and url == site.get("url")
    )


def configure(home, args):
    from .manage import installation, stop, start
    from companion import PushPlus, atomic, ProviderRejected

    config = installation(home)
    s = store(home)
    if config.get("demo"):
        raise RuntimeError("演示实例不绑定微信。请先运行 deploy.cmd 安装正式实例。")
    if args.yes:
        raise RuntimeError(
            "微信绑定需要本人扫码、填写密钥和手机确认；请去掉 --yes 后运行 wechat.cmd。"
        )
    print("微信是可选功能。以下步骤只连接你自己的 PushPlus；不会购买会员或短信等收费服务。")
    print(
        "先在 PushPlus 完成注册/登录、必要实名、关注服务号、开启开放接口，并扫描 ClawBot 绑定码。"
    )
    print("如果已启用安全IP白名单，当前检测到的出口：" + current_ip())
    print(
        "开发设置：https://www.pushplus.plus/uc-dev.html\nClawBot：https://www.pushplus.plus/uc-channel.html"
    )
    if input("打开官方设置并继续？[y/N] ").strip().lower() != "y":
        return
    webbrowser.open("https://www.pushplus.plus/uc-dev.html")
    if (
        not (Path(home) / "credentials.dpapi").exists()
        or input("是否更换本机保存的凭据？[y/N] ").strip().lower() == "y"
    ):
        token = getpass.getpass("PushPlus用户token（隐藏输入）：").strip()
        secret = getpass.getpass("SecretKey（隐藏输入）：").strip()
        s.save_credentials(token, secret)
    client = PushPlus(s)
    verified = False
    for attempt in range(5):
        input("完成官网设置后，向 ClawBot 主动发一句话，再按回车检查：")
        try:
            result = client.probe()
            if all(
                result.get(key) for key in ("realname_verified", "bot_bound", "has_conversation")
            ):
                verified = True
                break
            print("实名、绑定或主动对话尚未全部通过，请按官网提示完成。")
        except ProviderRejected as exc:
            print(str(exc) + "；403常见于开放接口未启用或安全IP不匹配。不会替你关闭白名单。")
    if not verified:
        raise RuntimeError("微信还未通过检查，已保留加密配置；稍后重新运行 wechat.cmd 即可。")
    binary = ensure_cloudflared(home)
    stop(home, args.wait)
    previous_enabled = s.profile().get("enabled", True)
    profile = s.profile()
    profile["enabled"] = False
    atomic(s.profile_path, json.dumps(profile, ensure_ascii=False, indent=2).encode())
    runtime = s.meta("reader_runtime")
    runtime.update(public=True, cloudflared=str(binary))
    s.put("reader_runtime", runtime)
    try:
        started = time.time()
        start(home, True)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if channel_ready(s, started):
                break
            time.sleep(2)
        else:
            raise RuntimeError(
                "手机阅读通道尚未连通，本机阅读仍可用。网络恢复后可再运行 wechat.cmd；不会改系统DNS或关闭TLS校验。"
            )
        from reader_portal import sync_portal

        fixed = sync_portal(s, client)
        if not fixed:
            raise RuntimeError("固定入口尚未生成，未发送没有入口的测试消息。")
        print("你的固定阅读入口：" + fixed)
        if input("现在向你自己的 ClawBot 发送一条接入测试？[y/N] ").strip().lower() != "y":
            return
        key = "onboarding:" + str(uuid.uuid4())
        receipt = client.send(
            key,
            "文献启发助手 · 接入测试",
            "你的私人阅读入口\n"
            + fixed
            + "\n每天09:00、21:00只发五篇简介和入口，详细内容在网页阅读。",
            "clawbot",
            template="txt",
        )
        deadline = time.monotonic() + 30
        while (
            receipt["status"] in ("accepted", "pending", "sending") and time.monotonic() < deadline
        ):
            time.sleep(2)
            receipt = client.check(key)
        if receipt["status"] != "sent":
            raise RuntimeError(
                "测试没有确认送达，不会盲目重发。请检查 ClawBot 的主动对话要求，稍后再试。"
            )
        if input("手机已看到测试，并能打开固定入口？[y/N] ").strip().lower() != "y":
            return
        profile = s.profile()
        profile["wechat"].update(enabled=True, binding_verified=True)
        atomic(s.profile_path, json.dumps(profile, ensure_ascii=False, indent=2).encode())
        print("微信接入完成。ClawBot每24小时/每10次下发需要你主动发一次消息，这是平台限制。")
    finally:
        profile = s.profile()
        profile["enabled"] = previous_enabled
        atomic(s.profile_path, json.dumps(profile, ensure_ascii=False, indent=2).encode())
