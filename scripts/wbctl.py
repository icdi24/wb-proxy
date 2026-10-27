#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb-proxy 控制台 —— 把 WorkBuddy AI 的额度变成 OpenAI 兼容接口。

所有运行时文件都放在 WB_PROXY_HOME（默认 ~/.wb-proxy），
skill 目录本身只读，不会被写入，因此可以放心 git pull。

用法：
  python wbctl.py doctor
  python wbctl.py start [--port 8788]
  python wbctl.py status
  python wbctl.py import
  python wbctl.py apply --client dsh
  python wbctl.py probe [--apply-ok]
  python wbctl.py stop
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_ROOT / "upstream"
HOME = Path(os.environ.get("WB_PROXY_HOME") or (Path.home() / ".wb-proxy")).expanduser()
HUB = HOME / "hub"
LOGDIR = HOME / "logs"
PIDFILE = HOME / "gateway.pid"
CONF = HOME / "config.json"
STAMP = HUB / ".wb-skill-stamp"
DEFAULT_PORT = 8788

# 本机回环请求必须绕开系统/环境代理，否则会被 http_proxy 劫持成 502。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# --------------------------------------------------------------------------- 基础

def out(msg: str = "") -> None:
    try:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.write(msg.encode(enc, "replace").decode(enc, "replace") + "\n")
        sys.stdout.flush()


def die(msg: str, code: int = 1):
    out("[x] " + msg)
    raise SystemExit(code)


def _w(text) -> int:
    """字符串显示宽度：CJK 算两格。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text))


def _pad(text, width: int) -> str:
    text = str(text)
    return text + " " * max(0, width - _w(text))


def load_conf() -> dict:
    if CONF.is_file():
        try:
            return json.loads(CONF.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_conf(data: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CONF.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def conf_port(cli_port=None) -> int:
    if cli_port:
        return int(cli_port)
    return int(load_conf().get("port") or DEFAULT_PORT)


def template_stamp() -> str:
    h = hashlib.sha256()
    for p in sorted(TEMPLATE.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(TEMPLATE)).encode())
            h.update(str(p.stat().st_size).encode())
    return h.hexdigest()[:16]


def ensure_hub(verbose: bool = True) -> None:
    """把 upstream/ 模板同步到运行时目录。账号与用量不在这里，不会被覆盖。"""
    if not TEMPLATE.is_dir():
        die("找不到 upstream/ 目录，skill 包不完整：" + str(TEMPLATE))
    stamp = template_stamp()
    current = STAMP.read_text(encoding="utf-8").strip() if STAMP.is_file() else ""
    if HUB.is_dir() and current == stamp:
        return
    HOME.mkdir(parents=True, exist_ok=True)
    LOGDIR.mkdir(parents=True, exist_ok=True)
    if HUB.is_dir():
        if verbose:
            out("[*] 检测到 upstream 有新版本，同步运行目录（保留账号/用量）…")
    elif verbose:
        out("[*] 首次部署：释放网关到 " + str(HUB))
    shutil.copytree(TEMPLATE, HUB, dirs_exist_ok=True)
    STAMP.write_text(stamp, encoding="utf-8")


# --------------------------------------------------------------------------- HTTP

def http_json(path: str, body=None, port: int = DEFAULT_PORT, token: str | None = None,
              timeout: float = 60.0, method: str | None = None):
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Panel-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "ignore")
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def health(port: int):
    try:
        s, b = http_json("/health", port=port, timeout=5)
        return b if s == 200 and isinstance(b, dict) else None
    except Exception:
        return None


def panel_token(port: int, password: str | None = None) -> str:
    pw = password or load_conf().get("panel_password") or "admin"
    s, b = http_json("/panel/login", {"password": pw}, port=port, timeout=15)
    if s != 200 or not isinstance(b, dict) or not b.get("token"):
        die("面板登录失败（HTTP %s）。密码不对？用 --panel-password 指定，或在 %s 里改 panel_password。"
            % (s, CONF))
    return b["token"]


def fetch_credits(port: int, token: str, uid: str) -> dict | None:
    """按账号拉一次积分明细（面板的「刷新积分」走的就是这个接口）。"""
    s, b = http_json("/accounts/credits/detail?uid=%s&refresh=1" % uid, port=port,
                     token=token, timeout=90)
    if s == 200 and isinstance(b, dict):
        return b.get("credits") if isinstance(b.get("credits"), dict) else None
    return None


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) != 0


# --------------------------------------------------------------------------- 命令

def cmd_doctor(args):
    port = conf_port(args.port)
    out("== 环境自检 ==")
    out("python        : %s (%d.%d.%d)" % (sys.executable, *sys.version_info[:3]))
    out("skill 目录    : %s" % SKILL_ROOT)
    out("运行时目录    : %s" % HOME)
    out("upstream 模板 : %s (%d 个文件)" % (TEMPLATE, len(list(TEMPLATE.rglob("*"))) if TEMPLATE.is_dir() else 0))
    out("运行目录      : %s%s" % (HUB, "" if HUB.is_dir() else "  (未释放)"))
    out("端口 %-6d   : %s" % (port, "空闲" if port_free(port) else "已被占用"))
    h = health(port)
    out("网关健康      : %s" % ("在线 " + json.dumps({"realm": h.get("realm"), "accounts": h.get("accounts")}, ensure_ascii=False) if h else "未运行"))
    if sys.version_info < (3, 9):
        out("[!] 需要 Python 3.9+（网关本身要求不高，脚本用了 3.9 的语法）")
    # 桌面客户端凭据
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "WorkBuddy AI" / "keyblob",
        Path(os.environ.get("APPDATA", "")) / "workbuddy-ai" / "keyblob",
        Path.home() / ".workbuddy" / "keyblob",
    ]
    found = [str(p) for p in candidates if p.is_file()]
    out("桌面 keyblob  : %s" % (found[0] if found else "未找到（导入账号前需要先登录桌面客户端）"))
    out("")
    out("下一步：python wbctl.py start")


def spawn_gateway(port: int, api_key: str | None, panel_pw: str | None) -> int:
    exe = sys.executable
    if os.name == "nt":
        cand = Path(exe).with_name("pythonw.exe")
        if cand.is_file():
            exe = str(cand)
    HOME.mkdir(parents=True, exist_ok=True)
    LOGDIR.mkdir(parents=True, exist_ok=True)
    logfile = LOGDIR / "gateway.log"
    argv = [exe, "wb_proxy.py", "--host", "127.0.0.1", "--port", str(port)]
    if api_key:
        argv += ["--api-key", api_key]
    if panel_pw:
        argv += ["--panel-password", panel_pw]
    fh = open(logfile, "ab")
    kwargs = dict(cwd=str(HUB), stdin=subprocess.DEVNULL, stdout=fh, stderr=fh, close_fds=True)
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.CREATE_NO_WINDOW)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)
    PIDFILE.write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def cmd_start(args):
    port = conf_port(args.port)
    if health(port):
        out("[=] 网关已在 %d 端口运行，无需重复启动" % port)
        return cmd_status(args)
    if not port_free(port):
        die("端口 %d 被别的程序占用，换一个：--port 8789" % port)
    ensure_hub()
    conf = load_conf()
    if args.port:
        conf["port"] = int(args.port)
        save_conf(conf)
    api_key = conf.get("api_key")
    panel_pw = conf.get("panel_password")
    out("[*] 启动网关（%s，端口 %d）…" % (HUB, port))
    pid = spawn_gateway(port, api_key, panel_pw)
    deadline = time.time() + 90
    last_note = 0.0
    while time.time() < deadline:
        h = health(port)
        if h:
            out("[ok] 网关就绪，pid=%d，出口=%s，账号=%s/%s"
                % (pid, h.get("realm"), h.get("accounts_ready"), h.get("accounts")))
            out("     OpenAI 兼容地址：http://127.0.0.1:%d/v1   面板：http://127.0.0.1:%d/"
                % (port, port))
            logfile = LOGDIR / "gateway.log"
            if h.get("accounts", 0) == 0:
                out("[!] 还没有导入账号 —— 先登录 WorkBuddy AI 桌面客户端，再跑：python wbctl.py import")
                if logfile.is_file():
                    out("    日志尾巴：" + str(logfile))
            return
        if time.time() - last_note > 10:
            last_note = time.time()
            out("    …等待首启动（首次会初始化目录与目录清单，最长 90 秒）")
        time.sleep(1.0)
    die("网关 90 秒内没起来，看日志：%s" % (LOGDIR / "gateway.log"))


def _proc_alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            # tasklist 的本地化输出在 zh-CN 上是 GBK，不能按文本解码
            r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                               capture_output=True, timeout=10)
            return str(pid).encode() in (r.stdout or b"")
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def cmd_stop(args):
    port = conf_port(args.port)
    pid = None
    if PIDFILE.is_file():
        try:
            pid = int(PIDFILE.read_text(encoding="utf-8").strip())
        except Exception:
            pid = None
    if pid and _proc_alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
        time.sleep(1.5)
        out("[ok] 已停止网关（pid=%d）" % pid)
    elif health(port):
        out("[!] 端口 %d 上还活着一个不是本脚本启动的网关，请手动结束它" % port)
    else:
        out("[=] 网关没在运行")
    try:
        PIDFILE.unlink()
    except Exception:
        pass


def cmd_status(args):
    port = conf_port(args.port)
    h = health(port)
    if not h:
        die("网关未运行。启动：python wbctl.py start")
    out("== 网关状态 ==")
    out("地址        : http://127.0.0.1:%d/v1" % port)
    out("出口        : %s%s" % (h.get("realm"), "（国际版 www.workbuddy.ai）" if h.get("realm") == "intl" else "（国内版 copilot.tencent.com）" if h.get("realm") == "cn" else ""))
    out("账号        : 就绪 %s / 共 %s" % (h.get("accounts_ready"), h.get("accounts")))
    out("API Key     : %s" % ("需要（已配置）" if h.get("api_key_required") else "不需要"))
    try:
        tok = panel_token(port)
    except SystemExit:
        raise
    except Exception:
        tok = None
    if tok:
        s, b = http_json("/accounts?realm=all", port=port, token=tok)
        if s == 200 and isinstance(b, dict):
            accounts = b.get("accounts", [])
            if getattr(args, "refresh", False):
                for a in accounts:
                    c = fetch_credits(port, tok, a.get("uid"))
                    if isinstance(c, dict):
                        a["credits"] = c
            out("")
            out(_pad("uid", 10) + _pad("昵称", 12) + _pad("出口", 7) + _pad("域名", 24) + "积分")
            for a in accounts:
                c = a.get("credits")
                remain = c.get("remain") if isinstance(c, dict) else c
                out(_pad(str(a.get("uid"))[:8], 10)
                    + _pad(a.get("nickname") or "-", 12)
                    + _pad(a.get("realm") or "-", 7)
                    + _pad(a.get("domain") or "-", 24)
                    + ("-" if remain is None else str(remain)))
    s, b = http_json("/v1/models", port=port, token=load_conf().get("api_key"), timeout=30)
    if s == 200 and isinstance(b, dict):
        ids = [m.get("id") for m in b.get("data", [])]
        out("")
        out("模型 %d 个：%s" % (len(ids), ", ".join(ids)))


def cmd_import(args):
    port = conf_port(args.port)
    if not health(port):
        die("网关未运行，先 start")
    tok = panel_token(port, args.panel_password)
    out("[*] 第一步：从桌面客户端进程回收 at-rest 密钥…")
    s, b = http_json("/accounts/import/desktop", {"recoverKey": True}, port=port, token=tok, timeout=180)
    if s != 200 or not isinstance(b, dict) or not b.get("ok"):
        msg = (b or {}).get("msg") if isinstance(b, dict) else b
        die("回收密钥失败：%s\n  提示：确认 WorkBuddy AI 桌面客户端已登录并正在运行（它是密钥的唯一来源）。" % msg)
    out("     keyId=%s  %s" % (b.get("keyId"), b.get("msg")))
    out("[*] 第二步：导入全部账号…")
    s, b = http_json("/accounts/import/desktop", {"all": True}, port=port, token=tok, timeout=300)
    if s != 200:
        die("导入失败：HTTP %s %s" % (s, b))
    imported = b.get("imported") or []
    out("[ok] 导入 %d 个账号：" % len(imported))
    for a in imported:
        out("     %s  %s  realm=%s  %s" % (str(a.get("uid"))[:8], a.get("nickname"), a.get("realm"), a.get("domain")))
    out("[*] 第三步：刷新积分/套餐…")
    for a in imported:
        c = fetch_credits(port, tok, a.get("uid"))
        if isinstance(c, dict):
            remain, size = c.get("remain"), c.get("size")
            out("     %s  %s  积分 %s%s" % (str(a.get("uid"))[:8], a.get("nickname") or "-", remain,
                                           "" if size is None else " / %s" % size))
        else:
            out("     %s  [i] 积分没取到（不影响使用，面板上可手动刷新）" % str(a.get("uid"))[:8])
    cmd_status(args)


def cmd_apply(args):
    port = conf_port(args.port)
    if not health(port):
        die("网关未运行，先 start")
    tok = panel_token(port, args.panel_password)
    s, agents = http_json("/agents", port=port, token=tok, timeout=60)
    if s != 200:
        die("读 /agents 失败：HTTP %s" % s)
    if args.list:
        out(_pad("客户端", 26) + _pad("已安装", 8) + _pad("已配置", 8) + _pad("协议", 11) + "配置文件")
        for c in agents.get("clients", []):
            out(_pad("%s (%s)" % (c.get("label") or c.get("id"), c.get("id")), 26)
                + _pad("是" if c.get("installed") else "否", 8)
                + _pad("是" if c.get("configured") else "否", 8)
                + _pad(c.get("protocol") or "-", 11)
                + ("; ".join(c.get("config_paths") or []))[:70])
        return
    client = args.client
    if not client:
        die("要指定 --client（dsh / claude-code / codex / opencode / crush / zcode），或 --list 看检测结果")
    models = [{"id": m} for m in args.models.split(",")] if args.models else \
             [{"id": m.get("id")} for m in (agents.get("models") or [])]
    if not models:
        s2, mb = http_json("/v1/models", port=port, token=load_conf().get("api_key"), timeout=30)
        models = [{"id": m.get("id")} for m in (mb.get("data") or [])] if s2 == 200 else []
    body = {
        "client": client,
        "base_url": args.base_url or ("http://127.0.0.1:%d/v1" % port),
        "models": models,
    }
    if args.model:
        body["model"] = args.model
    c = agents.get("gateway") or {}
    out("[*] 写入 %s 配置：%d 个模型" % (client, len(models)))
    s, b = http_json("/agents/apply", body, port=port, token=tok, timeout=120)
    if s != 200:
        die("apply 失败：HTTP %s %s" % (s, b))
    for f in b.get("files", []):
        out("     写入 %s%s" % (f.get("path"), "" if f.get("existed") else "（新建）"))
    if b.get("warnings"):
        out("[!] 警告：" + "; ".join(b["warnings"]))
    out("[ok] 完成。多数客户端需要重启后才会加载新 provider。")


def cmd_probe(args):
    port = conf_port(args.port)
    if not health(port):
        die("网关未运行，先 start")
    key = load_conf().get("api_key")
    s, b = http_json("/v1/models", port=port, token=key, timeout=30)
    if s != 200:
        die("/v1/models 失败：HTTP %s" % s)
    models = [m.get("id") for m in b.get("data", [])]
    if args.models:
        models = [m for m in args.models.split(",") if m in models] or args.models.split(",")
    out("[*] 逐个打真实请求（判真可用，不看目录）…")
    ok, bad = [], []
    for m in models:
        t0 = time.time()
        try:
            s, r = http_json("/v1/chat/completions",
                             {"model": m, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 32},
                             port=port, token=key, timeout=120)
            if s == 200 and isinstance(r, dict):
                ok.append(m)
                out("  [ok]   %-26s %.1fs credit=%s" % (m, time.time() - t0,
                                                        (r.get("usage") or {}).get("credit")))
            else:
                msg = r.get("error", {}).get("message") if isinstance(r, dict) else str(r)
                bad.append((m, "HTTP %s %s" % (s, str(msg)[:70])))
                out("  [--]   %-26s HTTP %s %s" % (m, s, str(msg)[:70]))
        except Exception as e:
            bad.append((m, repr(e)[:70]))
            out("  [--]   %-26s %s" % (m, repr(e)[:70]))
    out("")
    out("可用 %d 个：%s" % (len(ok), ", ".join(ok) if ok else "-"))
    (HOME / "probe-ok.json").write_text(json.dumps(ok, ensure_ascii=False, indent=2), encoding="utf-8")
    out("清单已写：%s" % (HOME / "probe-ok.json"))
    if args.apply_ok and ok:
        tok = panel_token(port, args.panel_password)
        s, agents = http_json("/agents", port=port, token=tok, timeout=60)
        body = {"client": args.client or "dsh",
                "base_url": "http://127.0.0.1:%d/v1" % port,
                "model": args.model or ok[0],
                "models": [{"id": m} for m in ok]}
        s, b = http_json("/agents/apply", body, port=port, token=tok, timeout=120)
        out("[ok] 已按可用清单写入 %s（%d 个模型，默认 %s）" % (body["client"], len(ok), body["model"]) if s == 200
            else "[x] apply 失败 HTTP %s %s" % (s, b))


def cmd_selftest(args):
    port = conf_port(args.port)
    h = health(port)
    if not h:
        die("网关未运行，先 start")
    out("[1/3] 健康检查 …… ok，账号 %s/%s" % (h.get("accounts_ready"), h.get("accounts")))
    key = load_conf().get("api_key")
    s, b = http_json("/v1/models", port=port, token=key, timeout=30)
    if s != 200:
        die("模型列表失败：HTTP %s" % s)
    ids = [m.get("id") for m in b.get("data", [])]
    out("[2/3] 模型目录 …… ok，%d 个" % len(ids))
    model = args.model or (ids[0] if ids else None)
    if not model:
        die("没有可用模型")
    t0 = time.time()
    s, r = http_json("/v1/chat/completions",
                     {"model": model, "messages": [{"role": "user", "content": "回复两个字：就绪"}]},
                     port=port, token=key, timeout=180)
    if s != 200:
        die("对话失败：HTTP %s %s" % (s, r))
    content = (r.get("choices") or [{}])[0].get("message", {}).get("content")
    out("[3/3] 真实对话 …… ok，%s 用时 %.1fs，返回 %r" % (model, time.time() - t0, content))
    out("")
    out("全部通过。把 http://127.0.0.1:%d/v1 填进任意 OpenAI 兼容客户端即可。" % port)


def cmd_logs(args):
    logfile = LOGDIR / "gateway.log"
    if not logfile.is_file():
        die("还没有日志：%s" % logfile)
    with open(logfile, "r", encoding="utf-8", errors="ignore") as fh:
        lines = fh.read().splitlines()[-args.lines:]
    out("\n".join(lines))


def cmd_upgrade(args):
    ensure_hub(verbose=False)
    STAMP.unlink(missing_ok=True)
    ensure_hub()
    out("[ok] 运行目录已同步到当前 skill 版本，账号/用量保持不变")


# --------------------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(prog="wbctl", description="WorkBuddy AI -> OpenAI 兼容网关控制台")
    ap.add_argument("--port", type=int, help="网关端口（默认 8788）")
    sub = ap.add_subparsers(dest="cmd")

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--port", type=int, help="网关端口")
        p.set_defaults(fn=fn)
        return p

    add("doctor", cmd_doctor, "环境自检")
    add("start", cmd_start, "启动网关（后台常驻）")
    add("stop", cmd_stop, "停止网关")
    add("status", cmd_status, "查看状态/账号/模型").add_argument(
        "--refresh", action="store_true", help="顺便刷新各账号积分")
    p = add("import", cmd_import, "从桌面客户端导入账号")
    p.add_argument("--panel-password", default=None)
    p = add("apply", cmd_apply, "把网关写进客户端配置")
    p.add_argument("--client", default=None, help="dsh / claude-code / codex / opencode / crush / zcode")
    p.add_argument("--model", default=None, help="默认模型")
    p.add_argument("--models", default=None, help="逗号分隔的模型清单")
    p.add_argument("--base-url", dest="base_url", default=None)
    p.add_argument("--list", action="store_true", help="只列出检测到的客户端")
    p.add_argument("--panel-password", default=None)
    p = add("probe", cmd_probe, "逐个模型打真实请求")
    p.add_argument("--models", default=None)
    p.add_argument("--apply-ok", dest="apply_ok", action="store_true", help="把可用清单写回客户端")
    p.add_argument("--client", default="dsh")
    p.add_argument("--model", default=None)
    p.add_argument("--panel-password", default=None)
    p = add("selftest", cmd_selftest, "端到端自检")
    p.add_argument("--model", default=None)
    p = add("logs", cmd_logs, "看网关日志")
    p.add_argument("--lines", type=int, default=40)
    add("upgrade", cmd_upgrade, "同步 upstream 到运行目录")

    args = ap.parse_args()
    if not getattr(args, "fn", None):
        ap.print_help()
        return 0
    args.fn(args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
