"""Install, configure and run a private single-user literature reader."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
import uuid

from .runtime import (
    ROOT,
    READER,
    CREATE_NO_WINDOW,
    guard_windows,
    default_home,
    read_json,
    write_json,
    run,
    ensure_node,
    npm,
    ensure_codex,
    ensure_login,
    choose_ports,
    ports_available,
    environment,
    store,
    build_fingerprint,
)


def service_active(home):
    import msvcrt

    path = Path(home) / "service.lock"
    if not path.exists():
        return False
    with path.open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            return False
        except OSError:
            return True


def stop(home, wait=120):
    if not service_active(home):
        return
    store(home).put("service_run", False)
    print("正在停止本实例；会保留进行中的研究结果，不强制终止其他程序。", flush=True)
    deadline = time.monotonic() + wait
    while service_active(home) and time.monotonic() < deadline:
        time.sleep(1)
    if service_active(home):
        raise RuntimeError("仍有任务在收尾，本次未覆盖构建或强制杀进程。稍后重试，或增加 --wait。")


def installation(home):
    value = read_json(Path(home) / "installation.json")
    if not value:
        raise RuntimeError("尚未安装，请先双击 deploy.cmd，或运行 python launch.py install。")
    return value


def register_instance(home):
    path = READER / ".runtime/instances.json"
    homes = read_json(path, [])
    value = str(Path(home).resolve())
    if value not in homes:
        write_json(path, homes + [value])


def require_other_instances_stopped(home):
    for value in read_json(READER / ".runtime/instances.json", []):
        other = Path(value)
        if other.resolve() != Path(home).resolve() and service_active(other):
            raise RuntimeError(
                "同一源码目录还有其他实例正在运行，请先停止该演示或正式实例，再构建或更新。"
            )


def apply_environment(config, home):
    child = environment(config["ports"], home, config["node"])
    for name in (
        "LITERATURE_COMPANION_HOME",
        "LITERATURE_PANEL_PORT",
        "LITERATURE_READER_PORT",
        "LITERATURE_WORKER_PORT",
        "LITERATURE_RELAY_PORT",
    ):
        os.environ[name] = child[name]
    return child


def database_command(node, home, filename):
    run(
        [
            node,
            "--import",
            (READER / "scripts/runtime-env.mjs").resolve().as_uri(),
            str(READER / "node_modules/wrangler/bin/wrangler.js"),
            "d1",
            "execute",
            "DB",
            "--local",
            "--persist-to",
            str(Path(home) / "reader-state"),
            "--config",
            str(READER / "wrangler.jsonc"),
            "--file",
            str(filename),
            "--yes",
        ],
        cwd=READER,
        timeout=120,
    )


def demo_data(home, node):
    from companion import now

    day = now().date().isoformat()
    cards = []
    rows = []
    for number in range(1, 6):
        cards.append(
            {
                "group": "preference" if number <= 3 else "exploration",
                "field": "功能演示",
                "title_cn": "演示：第" + str(number) + "篇示例",
                "title_original": "Demo paper " + str(number),
                "authors": "仅为界面演示，不是真实论文",
                "published_date": day,
                "source_url": "https://example.org/demo/" + str(number),
                "plain_summary": "这是安装验收用的演示数据，不是真实研究，也没有调用模型。登录后可开启真实文献检索。",
                "why": "演示推荐说明",
                "evidence_scope": "功能演示；不代表真实文献或研究结论",
                "publication_status": "演示数据",
                "learn": "体验粗读、切换、返回与位置恢复",
            }
        )
    digest = {
        "date": day,
        "search_scope": "功能演示，不是真实文献",
        "cards": cards,
        "english": "A model is a simplified description.\n模型是简化的描述。（演示句）",
        "practice": "演示练习：切换文章后返回，检查阅读位置。",
    }
    quote = lambda text: "'" + text.replace("'", "''") + "'"
    for key in ("latest", "digest:" + day):
        rows.append(
            "INSERT OR REPLACE INTO reader_settings VALUES ("
            + quote(key)
            + ","
            + quote(json.dumps(digest, ensure_ascii=False))
            + ");"
        )
    for number in range(1, 6):
        identifier = hashlib.sha256(
            json.dumps([2, day, number, "coarse", ""], separators=(",", ":")).encode()
        ).hexdigest()[:32]
        content = (
            "# 功能演示\n\n这是示例粗读，不是真实论文。\n\n## 体验阅读\n\n打开另一篇再返回，阅读器会保留位置。\n\n"
            + ("\n\n这是便于测试滚动位置的演示段落。" * 14)
        )
        rows.append(
            "INSERT OR REPLACE INTO reader_nodes(id,paper_date,paper_no,kind,parent_id,level,generation,status,title,content,scope,created_at,expires_at,error) VALUES ("
            + quote(identifier)
            + ","
            + quote(day)
            + ","
            + str(number)
            + ",'coarse',NULL,0,1,'ready',"
            + quote("粗读｜功能演示 " + str(number))
            + ","
            + quote(content)
            + ",'功能演示',"
            + str(int(time.time() * 1000))
            + ",NULL,'');"
        )
    filename = Path(home) / "demo-seed.sql"
    filename.write_text("\n".join(rows), encoding="utf-8")
    try:
        database_command(node, home, filename)
    finally:
        filename.unlink(missing_ok=True)
    s = store(home)
    s.put("digest:" + day, digest)


def autostart(home, enable=True):
    import winreg

    config = installation(home)
    # A separate value avoids replacing another assistant's login startup.
    name = "LiteratureCompanionOpenSource" + ("-Demo" if config.get("demo") else "")
    key = winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
    )
    try:
        if enable:
            silent = Path(sys.executable).with_name("pythonw.exe")
            if not silent.is_file():
                raise RuntimeError("找不到Python静默启动程序，未添加登录启动项。")
            command = subprocess.list2cmdline(
                [
                    str(silent),
                    "-X",
                    "utf8",
                    str(ROOT / "launch.py"),
                    "start",
                    "--home",
                    str(home),
                    "--no-browser",
                ]
            )
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


def start(home, no_browser=False):
    config = installation(home)
    register_instance(home)
    s = store(home)
    if Path(config["project_root"]).resolve() != ROOT.resolve():
        raise RuntimeError("源码目录已移动，请重新运行 deploy.cmd 更新本机路径。")
    if not service_active(home):
        if not ports_available(config["ports"]):
            config["ports"] = choose_ports(config["ports"]["panel"] - 1)
            write_json(Path(home) / "installation.json", config)
            print("原端口已被其他程序占用，已自动选择空闲端口。", flush=True)
        runtime = s.meta("reader_runtime") or {}
        runtime.setdefault("instance_id", uuid.uuid4().hex)
        s.put("reader_runtime", runtime)
        child = apply_environment(config, home)
        print("启动阅读器…", flush=True)
        with (Path(home) / "launcher.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(ROOT / "launch.py"),
                    "_serve",
                    "--home",
                    str(home),
                ],
                cwd=ROOT,
                env=child,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
        write_json(
            Path(home) / "service-process.json", {"pid": process.pid, "started_at": time.time()}
        )
    deadline = time.monotonic() + 80
    backend = "http://127.0.0.1:" + str(config["ports"]["worker"])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    ready = False
    while time.monotonic() < deadline:
        try:
            expected = (s.meta("reader_runtime") or {}).get("instance_id")
            request = urllib.request.Request(
                backend + "/api/health", headers={"X-Literature-Instance": expected or ""}
            )
            with opener.open(request, timeout=2) as response:
                if response.status == 200 and json.load(response).get("instance") == expected:
                    ready = True
                    break
        except Exception:
            pass
        time.sleep(1)
    if not ready:
        raise RuntimeError("服务未在80秒内就绪，请运行 doctor.cmd；日志只在本机数据目录。")
    from reader_gateway import secret

    url = "http://127.0.0.1:" + str(config["ports"]["reader"]) + "/enter/" + secret(s) + "?to=%2F"
    print(
        "阅读器已就绪。\n本机私人入口：" + url + "\n请勿把含私人入口码的地址发给他人。", flush=True
    )
    if not no_browser:
        webbrowser.open(url)
    return url


def doctor(home):
    config = installation(home)
    s = store(home)
    print(
        json.dumps(
            {
                "data_directory": str(home),
                "reader_directory": str(READER),
                "service_running": service_active(home),
                "clock": s.meta("clock"),
                "wechat_enabled": s.profile()["wechat"]["enabled"],
                "credentials_present": (Path(home) / "credentials.dpapi").is_file(),
                "public_reader": bool((s.meta("reader_runtime") or {}).get("public")),
                "ports": config["ports"],
                "demo": config.get("demo", False),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def preferences(s, interactive, force=False):
    if (s.meta("onboarding_done") and not force) or not interactive:
        return
    p = s.profile()
    value = input("关注哪些方向？用逗号分隔（回车沿用数学与计算、机器人）：").strip()
    if value:
        p["interests"] = [
            part.strip() for part in value.replace("，", ",").split(",") if part.strip()
        ]
    for key, label in [
        ("math_level", "数学基础"),
        ("python_level", "编程基础"),
        ("english_level", "英语基础"),
    ]:
        value = input(label + "（回车沿用默认）：").strip()
        if value:
            p[key] = value
    from companion import atomic

    atomic(s.profile_path, json.dumps(p, ensure_ascii=False, indent=2).encode())
    s.put("onboarding_done", True)


def install(args, home):
    guard_windows()
    home.mkdir(parents=True, exist_ok=True)
    require_other_instances_stopped(home)
    prior = read_json(home / "installation.json", {})
    node = ensure_node(home, args.portable_node)
    fingerprint = build_fingerprint()
    needs_build = (
        args.rebuild
        or read_json(READER / ".runtime/build.json", {}).get("build_hash") != fingerprint
        or not (READER / "dist/server/wrangler.json").exists()
    )
    if service_active(home):
        stop(home, args.wait)
    lock_hash = hashlib.sha256((READER / "package-lock.json").read_bytes()).hexdigest()
    stamp = READER / ".runtime/dependencies.json"
    if (
        read_json(stamp, {}).get("lock_hash") != lock_hash
        or not (READER / "node_modules/vinext").is_dir()
    ):
        print("安装锁定版本的网页依赖…", flush=True)
        run(npm(node) + ["ci", "--no-audit", "--no-fund"], cwd=READER, timeout=1800)
        write_json(stamp, {"lock_hash": lock_hash})
    if needs_build:
        print("构建阅读器…", flush=True)
        run([node, READER / "scripts/framework.mjs", "build"], cwd=READER)
        write_json(READER / ".runtime/build.json", {"build_hash": fingerprint})
    for migration in sorted((READER / "migrations").glob("*.sql")):
        database_command(node, home, migration)
    ports = prior.get("ports") or choose_ports(args.base_port)
    config = {
        "version": 1,
        "project_root": str(ROOT),
        "node": str(node),
        "python": sys.executable,
        "ports": ports,
        "demo": args.demo,
        "build_hash": fingerprint,
    }
    write_json(home / "installation.json", config)
    apply_environment(config, home)
    s = store(home)
    runtime = s.meta("reader_runtime") or {}
    runtime.update(
        enabled=True,
        project_root=str(READER),
        node=str(node),
        persist_path=str(home / "reader-state"),
    )
    runtime.setdefault("public", False)
    s.put("reader_runtime", runtime)
    p = s.profile()
    p["enabled"] = False
    from companion import atomic

    atomic(s.profile_path, json.dumps(p, ensure_ascii=False, indent=2).encode())
    if args.demo:
        p["reading"]["prepare_coarse"] = False
        atomic(s.profile_path, json.dumps(p, ensure_ascii=False, indent=2).encode())
        demo_data(home, node)
    elif not args.no_login:
        executable = ensure_codex(home, node)
        ensure_login(executable, not args.yes, home)
        s.put("codex_executable", str(executable))
        preferences(s, not args.yes)
        if args.wechat:
            from .wechat import configure

            configure(home, args)
        p = s.profile()
        p["enabled"] = True
        p["reading"]["prepare_coarse"] = True
        atomic(s.profile_path, json.dumps(p, ensure_ascii=False, indent=2).encode())
    if args.autostart:
        autostart(home)
    print(
        "安装已完成。"
        + (
            "当前是无模型演示模式。"
            if args.demo
            else "尚未登录时保持暂停；真实研究会使用你的 Codex 套餐额度。"
        ),
        flush=True,
    )
    if not args.no_start:
        start(home, args.no_browser)


def update(args, home):
    from .runtime import get_json, extract_zip
    import tempfile

    require_other_instances_stopped(home)
    stop(home, args.wait)
    repo = read_json(ROOT / "project.json")["repository"]
    if not __import__("re").fullmatch(r"[-\w.]+/[-\w.]+", repo):
        raise RuntimeError("仓库标识无效。")
    commit = get_json("https://api.github.com/repos/" + repo + "/commits/main")["sha"]
    if not __import__("re").fullmatch("[a-f0-9]{40}", commit):
        raise RuntimeError("版本标识无效。")
    print("下载 GitHub 已固定的版本 " + commit[:10] + "…", flush=True)
    request = urllib.request.Request(
        "https://api.github.com/repos/" + repo + "/zipball/" + commit,
        headers={"User-Agent": "LiteratureCompanionUpdater/0.1"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise RuntimeError("源码包超过预期大小，未覆盖本地文件。")
    with tempfile.TemporaryDirectory(prefix="literature-update-") as folder:
        archive = Path(folder) / "source.zip"
        archive.write_bytes(data)
        extract_zip(archive, Path(folder) / "unpacked")
        extracted = list((Path(folder) / "unpacked").iterdir())
        if len(extracted) != 1 or not (extracted[0] / "launch.py").is_file():
            raise RuntimeError("下载的源码结构不符。")
        for path in extracted[0].rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(extracted[0])
            target = (ROOT / relative).resolve()
            if not target.is_relative_to(ROOT.resolve()) or any(
                part in (".git", ".runtime", ".wrangler", "node_modules", "dist")
                for part in relative.parts
            ):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    # Re-execute the freshly downloaded installer; old imported helpers must
    # not determine the upgraded version's requirements or migration behavior.
    run(
        [
            sys.executable,
            "-X",
            "utf8",
            ROOT / "launch.py",
            "install",
            "--home",
            home,
            "--yes",
            "--no-browser",
        ]
        + (["--demo"] if installation(home).get("demo") else []),
        timeout=2400,
    )


def main():
    parser = argparse.ArgumentParser(description="文献启发助手：自动安装、启动和微信接入")
    parser.add_argument(
        "command",
        nargs="?",
        default="install",
        choices=[
            "install",
            "start",
            "stop",
            "doctor",
            "update",
            "wechat",
            "profile",
            "autostart",
            "no-autostart",
            "_serve",
        ],
    )
    parser.add_argument("--home", type=Path)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--no-login", action="store_true")
    parser.add_argument("--no-start", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--wechat", action="store_true")
    parser.add_argument("--autostart", action="store_true")
    parser.add_argument("--portable-node", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--base-port", type=int, default=18740)
    parser.add_argument("--wait", type=int, default=120)
    args = parser.parse_args()
    home = (args.home or default_home(args.demo)).resolve()
    if sys.stdout is None or sys.stderr is None:
        home.mkdir(parents=True, exist_ok=True)
        silent_log = (home / "launcher.log").open("a", encoding="utf-8")
        sys.stdout = sys.stderr = silent_log
    try:
        guard_windows()
        if args.command == "install":
            install(args, home)
        elif args.command == "start":
            start(home, args.no_browser)
        elif args.command == "stop":
            stop(home, args.wait)
            print("本实例已停止。")
        elif args.command == "doctor":
            doctor(home)
        elif args.command == "update":
            update(args, home)
        elif args.command == "profile":
            preferences(store(home), True, True)
        elif args.command == "wechat":
            from .wechat import configure

            configure(home, args)
        elif args.command in ("autostart", "no-autostart"):
            autostart(home, args.command == "autostart")
        elif args.command == "_serve":
            config = installation(home)
            apply_environment(config, home)
            s = store(home)
            from companion import service

            service(s)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print("未完成：" + str(exc), file=sys.stderr)
        raise SystemExit(1)
