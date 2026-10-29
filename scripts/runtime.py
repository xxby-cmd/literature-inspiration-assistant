"""Portable prerequisites, checksummed downloads and isolated local state."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
READER = ROOT / "reader"
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def guard_windows():
    if os.name != "nt" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("当前自动部署支持 Windows 10/11 x64；其他系统尚未通过完整验收。")
    if sys.version_info < (3, 11):
        raise RuntimeError("需要 Python 3.11 或更新版本，请运行 deploy.cmd 自动准备。")


def default_home(demo=False):
    return Path(
        os.environ.get(
            "LITERATURE_COMPANION_HOME",
            str(
                Path(os.environ.get("LOCALAPPDATA", Path.home()))
                / "LiteratureCompanion"
                / ("demo-data" if demo else "data")
            ),
        )
    )


def read_json(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def run(args, cwd=None, capture=False, input=None, timeout=900, env=None):
    result = subprocess.run(
        [str(x) for x in args],
        cwd=cwd or ROOT,
        input=input,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
        timeout=timeout,
        env=env,
    )
    if not capture:
        if result.stdout:
            print(result.stdout, flush=True)
        if result.stderr:
            print(result.stderr, flush=True)
    if result.returncode:
        if capture and result.stderr:
            print(result.stderr[-2500:])
        raise RuntimeError(Path(str(args[0])).name + " 执行失败，退出码 " + str(result.returncode))
    return result.stdout if capture else ""


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "LiteratureCompanionInstaller/0.1", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.load(response)


def download(url, destination, sha256):
    if not re.fullmatch("[a-fA-F0-9]{64}", sha256):
        raise ValueError("缺少有效的官方 SHA256，未下载执行。")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (
        destination.exists()
        and hashlib.sha256(destination.read_bytes()).hexdigest() == sha256.lower()
    ):
        return destination
    temporary = destination.with_suffix(destination.suffix + ".download")
    digest = hashlib.sha256()
    size = 0
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "LiteratureCompanionInstaller/0.1"}
        )
        with (
            urllib.request.urlopen(request, timeout=90) as response,
            temporary.open("wb") as output,
        ):
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > 350 * 1024 * 1024:
                    raise RuntimeError("安装文件超过预期大小，已停止。")
                digest.update(block)
                output.write(block)
        if digest.hexdigest() != sha256.lower():
            raise RuntimeError("安装文件 SHA256 与官方不符，未执行。")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def extract_zip(archive, destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination):
                raise RuntimeError("压缩包包含越界路径。")
        package.extractall(destination)


def node_suitable(path):
    try:
        version = run([path, "--version"], capture=True, timeout=10).strip().lstrip("v")
        return tuple(map(int, version.split(".")[:3])) >= (22, 18, 0)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
        return False


def ensure_node(home, force_portable=False):
    existing = shutil.which("node")
    if not force_portable and existing and node_suitable(existing):
        return Path(existing)
    tools = Path(home).parent / "tools/node"
    for path in tools.glob("node-v*-win-x64/node.exe"):
        if node_suitable(path):
            return path
    print("准备官方 Node.js LTS 便携版（不修改全局 Node 或系统设置）…", flush=True)
    versions = get_json("https://nodejs.org/dist/index.json")
    version = next(
        v["version"]
        for v in versions
        if v.get("lts")
        and "win-x64-zip" in v.get("files", [])
        and tuple(map(int, v["version"].lstrip("v").split("."))) >= (22, 18, 0)
    )
    filename = "node-" + version + "-win-x64.zip"
    base = "https://nodejs.org/dist/" + version + "/"
    with urllib.request.urlopen(base + "SHASUMS256.txt", timeout=40) as response:
        checksums = response.read().decode()
    expected = next(
        line.split()[0] for line in checksums.splitlines() if line.split()[-1] == filename
    )
    archive = download(base + filename, tools / filename, expected)
    extract_zip(archive, tools)
    result = tools / ("node-" + version + "-win-x64/node.exe")
    if not node_suitable(result):
        raise RuntimeError("Node.js 安装后版本检查未通过。")
    return result


def npm(node):
    cli = Path(node).parent / "node_modules/npm/bin/npm-cli.js"
    if not cli.exists():
        raise RuntimeError("找不到与 Node.js 配套的 npm，请选择官方完整 Node 安装。")
    return [str(node), str(cli)]


def codex_candidates():
    direct = shutil.which("codex")
    if direct and Path(direct).suffix.lower() == ".exe":
        yield Path(direct)
    if direct:
        for root in (
            Path(direct).parent / "node_modules/@openai",
            Path(direct).parent.parent / "node_modules/@openai",
        ):
            if root.is_dir():
                for package in root.glob("codex*"):
                    yield from package.rglob("codex.exe")


def ensure_codex(home, node):
    for candidate in codex_candidates():
        if candidate.is_file() and codex_suitable(candidate):
            return candidate
    prefix = Path(home).parent / "tools/codex"
    native = list(prefix.glob("node_modules/@openai/codex*/**/codex.exe"))
    for candidate in native:
        if codex_suitable(candidate):
            return candidate
    print("准备官方 Codex CLI；不会配置付费 API 密钥。", flush=True)
    run(
        npm(node)
        + ["install", "--prefix", str(prefix), "--no-audit", "--no-fund", "@openai/codex"],
        timeout=1200,
    )
    native = list(prefix.glob("node_modules/@openai/codex*/**/codex.exe"))
    for candidate in native:
        if codex_suitable(candidate):
            return candidate
    raise RuntimeError("Codex 安装后功能检查未通过，请按 docs/安装部署.md 的官方入口检查。")


def codex_suitable(executable):
    """Help probes do not authenticate, generate content or change global CLI."""
    try:
        top = run([executable, "--help"], capture=True, timeout=20)
        execution = run([executable, "exec", "--help"], capture=True, timeout=20)
        return all(flag in top for flag in ("--search", "--ask-for-approval")) and all(
            flag in execution
            for flag in (
                "--ephemeral",
                "--output-schema",
                "--skip-git-repo-check",
                "--sandbox",
                "--output-last-message",
            )
        )
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return False


def ensure_login(executable, interactive=True, home=None):
    if home is None:
        raise ValueError("需要独立的应用数据目录。")
    from codex_session import clean_environment

    env = clean_environment(home)
    result = subprocess.run(
        [str(executable), "login", "status"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        timeout=20,
        env=env,
    )
    status = result.stdout + result.stderr
    if result.returncode == 0 and "ChatGPT" in status:
        return
    if result.returncode == 0:
        raise RuntimeError(
            "检测到非 ChatGPT 登录方式；本项目不使用付费 API。请自行运行 codex login 选择 ChatGPT 后重试，不会替你退出已有账号。"
        )
    if not interactive:
        raise RuntimeError("需要本人完成 ChatGPT 登录：" + str(executable) + " login")
    print("请在官方浏览器页面完成 ChatGPT 登录，完成后自动继续。", flush=True)
    run([executable, "login"], timeout=1800, env=env)
    ensure_login(executable, False, home)


def ensure_cloudflared(home):
    existing = shutil.which("cloudflared")
    if existing:
        return Path(existing)
    release = get_json("https://api.github.com/repos/cloudflare/cloudflared/releases/latest")
    asset = next(a for a in release["assets"] if a["name"] == "cloudflared-windows-amd64.exe")
    digest = str(asset.get("digest", "")).removeprefix("sha256:")
    if not re.fullmatch("[a-fA-F0-9]{64}", digest):
        body = release.get("body", "")
        match = re.search(r"cloudflared-windows-amd64\.exe[^\n]*?\b([a-fA-F0-9]{64})\b", body)
        if not match:
            raise RuntimeError(
                "官方发布未提供可核验的 SHA256；请手动安装 Cloudflare 官方 cloudflared 后重试。"
            )
        digest = match[1]
    return download(
        asset["browser_download_url"], Path(home).parent / "tools/cloudflared.exe", digest
    )


def choose_ports(base=18740):
    for candidate in range(base, min(base + 10000, 65529), 1000):
        sockets = []
        try:
            ports = {
                "panel": candidate + 1,
                "reader": candidate + 3,
                "worker": candidate + 4,
                "relay": candidate + 5,
            }
            for port in ports.values():
                sock = socket.socket()
                sockets.append(sock)
                sock.bind(("127.0.0.1", port))
            return ports
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError("没有找到空闲本机端口，请使用 --base-port 指定另一段。")


def environment(ports, home, node=None):
    env = os.environ.copy()
    env["LITERATURE_COMPANION_HOME"] = str(home)
    env["PYTHONUTF8"] = "1"
    for name, value in ports.items():
        env["LITERATURE_" + name.upper() + "_PORT"] = str(value)
    if node:
        env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
    for key in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "CHATGPT_BASE_URL",
    ):
        env.pop(key, None)
    return env


def ports_available(ports):
    held = []
    try:
        for port in ports.values():
            if type(port) is not int or not 1024 <= port <= 65535:
                return False
            sock = socket.socket()
            held.append(sock)
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        for sock in held:
            sock.close()


def store(home):
    scripts = str(ROOT / "companion/scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from companion import Store

    return Store(home)


def build_fingerprint():
    digest = hashlib.sha256()
    paths = [
        READER / "package.json",
        READER / "package-lock.json",
        READER / "vite.config.ts",
        READER / "wrangler.jsonc",
    ]
    for folder in ("app", "lib", "worker", "scripts", "types", "migrations"):
        paths.extend(p for p in (READER / folder).rglob("*") if p.is_file())
    for path in sorted(paths):
        digest.update(path.relative_to(READER).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
