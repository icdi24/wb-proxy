#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""让 dsh 同时看到两家出口：WB 国际版 + WB 国内版（模型分组区分）。

原理：网关按请求头 `X-Realm` 选择出口。dsh 的 llm-pi-ai provider 支持
`headers`，于是第二个 provider 指向**同一个网关**、只多一个头，
两个 provider 在模型选择器里就是两组，互不覆盖。

幂等：重复执行只会重写 `wb-cn` 块。写完用 yaml 校验，失败不落盘。

用法：
  python scripts/two_exits.py                 # 默认网关 8788，写 dsh 配置
  python scripts/two_exits.py --realm cn --id wb-cn --display "WB 国内版"
  python scripts/two_exits.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_GATEWAY = "http://127.0.0.1:8788"
DEFAULT_CFG = Path.home() / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml"
SKIP_DEFAULT = {"glm-5.1"}   # 上游要求 reasoning effort，网关目录没申报，配了必 400
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def catalog(gateway: str, realm: str) -> list:
    req = urllib.request.Request(gateway + "/v1/models", headers={"X-Realm": realm} if realm else {})
    with _OPENER.open(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def model_lines(m: dict) -> list:
    out = ["          - id: %s" % m["id"]]
    ctx = m.get("max_input_tokens") or m.get("context_length")
    if ctx:
        out.append("            contextWindow: %d" % int(ctx))
    out_tok = m.get("max_output_tokens") or m.get("max_completion_tokens")
    if out_tok:
        out.append("            maxTokens: %d" % int(out_tok))
    eff = m.get("reasoning_efforts")
    pairs = []
    if isinstance(eff, dict):
        pairs = list(eff.items())
    elif isinstance(eff, (list, tuple)):
        pairs = [(x, x) for x in eff]
    if pairs:
        out.append("            reasoningEfforts:")
        for k, v in pairs:
            out.append("              %s: %s" % (k, v or k))
    mods = m.get("input_modalities") or ["text"]
    out.append("            input:")
    for mod in mods:
        out.append("              - %s" % mod)
    return out


def build_block(models: list, pid: str, display: str, gateway: str, realm: str) -> list:
    lines = [
        "      %s:" % pid,
        "        displayName: %s" % display,
        "        apiKeyEnv: WB_PROXY_API_KEY",
        "        api: openai-completions",
        "        baseURL: %s/v1" % gateway,
        "        headers:",
        "          X-Realm: %s" % realm,
        "        compat:",
        "          thinkingFormat: openai",
        "          supportsReasoningEffort: true",
        "        retryPolicy:",
        "          mode: normal",
        "          maxRetries: 8",
        "          backoff:",
        "            initialDelayMs: 2000",
        "            maxDelayMs: 30000",
        "            jitterRatio: 0.1",
        "        models:",
    ]
    for m in models:
        lines += model_lines(m)
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY)
    ap.add_argument("--config", default=str(DEFAULT_CFG), help="dsh 的 cordis.patch.yml")
    ap.add_argument("--realm", default="cn", help="要新增的出口：cn / intl")
    ap.add_argument("--id", default="wb-cn", help="新 provider 的 id")
    ap.add_argument("--display", default="WB 国内版")
    ap.add_argument("--rename-main", default="WB 国际版", help="把原 wb-proxy 的显示名改成这个；留空不改")
    ap.add_argument("--skip", default=",".join(sorted(SKIP_DEFAULT)), help="跳过的模型 id（逗号分隔）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = Path(args.config)
    if not cfg.is_file():
        print("[x] 找不到 %s —— 先用网关 apply 一次 dsh 配置" % cfg)
        return 1
    gateway = args.gateway.rstrip("/")
    skip = {x.strip() for x in args.skip.split(",") if x.strip()}
    try:
        models = [m for m in catalog(gateway, args.realm) if m["id"] not in skip]
    except Exception as exc:
        print("[x] 取 %s 出口模型目录失败：%s（网关没起？）" % (args.realm, exc))
        return 1
    if not models:
        print("[x] %s 出口没有可用模型" % args.realm)
        return 1

    lines = cfg.read_text(encoding="utf-8").splitlines()

    if args.rename_main:
        for i, l in enumerate(lines):
            if l.strip().startswith("displayName:") and "wb" in l.lower() and args.display not in l:
                head = l.split(":", 1)[0]
                lines[i] = "%s: %s" % (head, args.rename_main)
                break

    new_block = build_block(models, args.id, args.display, gateway, args.realm)

    start = next((i for i, l in enumerate(lines) if l.rstrip() == "      %s:" % args.id), None)
    if start is not None:
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].startswith("- ") or re.match(r"^      \S", lines[j]):
                end = j
                break
        lines[start:end] = new_block
    else:
        anchor = next((i for i, l in enumerate(lines) if l.rstrip() in ("      wb-proxy:", "      %s:" % args.id)), None)
        if anchor is None:
            print("[x] 配置里没有 wb-proxy 块，先让网关 apply 一次")
            return 1
        end = len(lines)
        for j in range(anchor + 1, len(lines)):
            if lines[j].startswith("- ") or re.match(r"^      \S", lines[j]):
                end = j
                break
        lines[end:end] = new_block

    new_text = "\n".join(lines) + "\n"
    try:
        import yaml
        data = yaml.safe_load(new_text)
        providers = {}
        for e in data:
            if e.get("id") == "llm-pi-ai":
                providers = (e.get("config") or {}).get("providers") or {}
        if args.id not in providers:
            print("[x] 校验失败：新 provider 没写进去")
            return 1
        n_new = len(providers[args.id]["models"])
    except ImportError:
        n_new = -1
        print("[i] 没有 pyyaml，跳过结构校验")

    if args.dry_run:
        print(new_text)
        return 0

    bak = cfg.with_name(cfg.name + ".bak-%s" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(cfg, bak)
    cfg.write_text(new_text, encoding="utf-8")
    print("[ok] 写入 %s" % cfg)
    print("     备份 %s" % bak.name)
    print("     %s (%s): %d 个模型" % (args.display, args.id, n_new))
    print("     重启 dsh 后，模型选择器里会多出这一组")
    return 0


if __name__ == "__main__":
    sys.exit(main())
