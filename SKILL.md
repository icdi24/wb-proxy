---
name: wb-proxy
description: 把 WorkBuddy AI 桌面客户端的额度变成一个本机 OpenAI 兼容接口（默认 http://127.0.0.1:8788/v1），并把任意 agent 客户端（dsh / Claude Code / Codex / OpenCode / Crush / ZCode）指向它。当用户说「用 WorkBuddy 的额度跑别的客户端」「wb-proxy」「WorkBuddy 转 OpenAI 接口」「反代 WorkBuddy AI」「把网关写进 dsh 配置」时使用。
agent_created: true
---

# wb-proxy

把本机已登录的 **WorkBuddy AI**（国际版 `www.workbuddy.ai` / 国内版 `copilot.tencent.com`）
的模型额度，转成一个标准的 **OpenAI 兼容**接口。之后 dsh、Claude Code、Codex 这些
客户端就能直接吃 WorkBuddy 的模型（`deepseek-v4.1-flash`、`hy3`、`glm-5.3`、`kimi-k3`…），
不用另买 API Key。

网关本体来自开源项目 [workbuddy2api-hub](https://github.com/ardeyouxipianyi/workbuddy2api-hub)（MIT），
已 vendored 在 `upstream/`，本 skill 负责**部署、常驻、导入账号、写客户端配置、验收**。

---

## 30 秒上手

```bash
# 0) 前提：WorkBuddy AI 桌面客户端已安装、已登录、正在运行（账号密钥从它的进程内存里取）

python scripts/wbctl.py doctor          # 环境自检
python scripts/wbctl.py start           # 启动网关（后台常驻，默认 8788）
python scripts/wbctl.py import          # 从桌面客户端导入账号 + 刷新积分
python scripts/wbctl.py apply --client dsh   # 把网关写进客户端配置
python scripts/wbctl.py selftest        # 端到端自检（真发一次对话）
```

看到 `全部通过。把 http://127.0.0.1:8788/v1 填进任意 OpenAI 兼容客户端即可。` 就算成了。

Windows 用户也可以直接双击 `bin/wb-proxy.cmd`（等价于 `start`）。

### 想让网关一直活着？

`start` 起的网关在普通双击场景够用，但如果它是在 IDE 内建终端、CI、沙箱 shell
里被拉起来的，父进程一退，网关会被**一并回收** —— 症状很迷惑人：`start` 明明确认
「网关就绪」，下一条命令却报「网关未运行」。

Windows 上用计划任务常驻，重启后也会自动恢复：

```bash
python scripts/wbctl.py install-task      # 注册「登录时启动」+ 立即拉起
python scripts/wbctl.py uninstall-task    # 撤销
```

它会生成 `~/.wb-proxy/start-gateway.cmd`（真正的启动脚本，日志重定向到
`~/.wb-proxy/logs/gateway.log`），再注册计划任务 `wb-proxy-gateway` 指向它。
计划任务由任务计划程序直接拉起，不在任何 shell 的作业里，因此不会被回收。

> 若 `schtasks` 被安全软件拦截，脚本会提示改用 PowerShell 的
> `Register-ScheduledTask`，动作指向那个 `.cmd` 即可。

---

## 目录约定（重要）

| 位置 | 用途 | 会不会被覆盖 |
|---|---|---|
| `<skill>/upstream/` | 上游网关源码模板，**只读** | `git pull` 更新 |
| `<skill>/scripts/wbctl.py` | 控制台 | `git pull` 更新 |
| `~/.wb-proxy/hub/` | 运行时：网关副本 + `accounts/` + `catalog/` | 升级时同步代码，**不动账号** |
| `~/.wb-proxy/logs/gateway.log` | 网关日志 | 追加 |
| `~/.wb-proxy/config.json` | 端口 / api_key / 面板密码 | 保留 |

`~/.wb-proxy` 可以用环境变量 `WB_PROXY_HOME` 改到别的盘。

---

## 命令参考

| 命令 | 作用 | 关键参数 |
|---|---|---|
| `doctor` | 环境自检：python、模板、端口、keyblob | |
| `start` | 后台启动网关并等它 ready（90s 超时） | `--port` |
| `stop` | 停网关（按 pidfile） | |
| `status` | 出口 / 账号 / 积分 / 模型清单 | `--refresh` 顺便刷积分 |
| `import` | 回收 at-rest 密钥 → 导入全部账号 → 刷积分 | `--panel-password` |
| `apply` | 把网关写进客户端配置 | `--client`、`--model`、`--models`、`--list` |
| `probe` | 逐个模型**打真实请求**，产出可用清单 | `--models`、`--apply-ok` |
| `selftest` | 健康 + 模型目录 + 真对话 | `--model` |
| `logs` | 看网关日志尾部 | `--lines` |
| `upgrade` | 把 `upstream/` 新版本同步到运行目录 | |
| `install-task` | 注册 Windows「登录时启动」计划任务并立即拉起（常驻） | `--port` |
| `uninstall-task` | 删除计划任务与启动脚本（不停已经跑起来的进程） | |

支持的客户端（`apply --client`）：`dsh`、`claude-code`、`codex`、`opencode`、`crush`、`zcode`。
先 `apply --list` 看哪些已安装 —— 它会同时打印每个客户端的配置文件路径。

---

## 必须知道的几条事实

**1. 账号密钥只能从运行中的桌面客户端取。**
磁盘上的凭据是 `$wbEncrypted`（AES-256-GCM，主密钥在原生模块里）；`import` 走的是
「扫进程内存 → `sha256(候选)[:16]` 比对 keyblob 里的 `protectorKeyId`」这条路。
所以：**WorkBuddy 客户端没开 / 没登录，`import` 必失败**，先让它登录再跑。

**2. 出口（realm）决定用哪个账号，模型决定出口。**
- `gpt-*`、`gemini-*`、`grok-4.7` → 强制走**国际版**
- `deepseek-v4-pro`、`minimax-*` 等 → 强制走**国内版**
- 其余（`deepseek-v4.1-flash`、`hy3`、`glm-5.3`、`kimi-*`…）→ **跟随当前出口**

当前出口默认 `intl`。要切：

```bash
# 面板切（推荐，立即生效）
# 打开 http://127.0.0.1:8788/ → 顶部选出口
# 或写死再重启网关：
echo {"realm":"cn"} > ~/.wb-proxy/hub/accounts/active_realm.json
python scripts/wbctl.py stop && python scripts/wbctl.py start
```

跨出口调模型会得到一句很清楚的 400：「模型 X 只在国内版提供，但当前 Key 绑定的是国际版出口」。

**2b. 想让两家出口并存，别开两个网关 —— 加一个请求头就行。**
网关支持按请求头 `X-Realm` 选出口，而 dsh 的 pi-ai provider 支持 `headers`：
同一个 `baseURL`，第二个 provider 只多一个 `X-Realm: cn`，模型选择器里就是两组。

```bash
python scripts/two_exits.py            # 默认给 dsh 加 wb-cn（国内版），并把原 provider 改名为「WB 国际版」
python scripts/two_exits.py --dry-run  # 只看要写什么
```

- 幂等：重复执行只重写 `wb-cn` 块，不动其它内容；写完用 YAML 校验，失败不落盘。
- 国际版 11 个模型 + 国内版 18 个模型，互不覆盖；共有模型会出现两次，靠分组区分。
- 默认跳过 `glm-5.1`：上游要求 reasoning effort，网关目录没申报，配了必 400。
- **改完要重启 dsh**（provider 只在启动时加载）。
- 国内版独有：`deepseek-v4-pro`、`minimax-m3`、`kimi-k3-1`、`kimi-k2.7`、`glm-5v-turbo`、
  `step-5-preview`、`space-bunny`、`mimo-v2.6-pro`。

**3. 目录里 24 个模型不代表 24 个能用。**
非 PRO 账号调 `gpt-*` / `gemini-*` 一律 503。**用 `probe` 打真实请求判断**，
它会把可用清单写到 `~/.wb-proxy/probe-ok.json`，`--apply-ok` 可顺手只把可用的写进客户端配置。

**4. 本机回环请求必须绕开系统代理。**
脚本内部已用 `ProxyHandler({})` 强制直连；自己写脚本调试时也要这样，
否则 `http_proxy` 会把 `127.0.0.1:8788` 劫持成 502。

**5. 面板与坐标。**
面板 `http://127.0.0.1:8788/`，默认密码 `admin`（`config.json` 的 `panel_password` 可改）。
所有接口要么带 `X-Panel-Token`（登录后拿），要么在网关设了 `--api-key` 时带 `Authorization: Bearer`。

---

## 换账号

网关存的是**账号凭据的副本**（`~/.wb-proxy/hub/accounts/<uid>.json`），不是对桌面
客户端登录态的实时引用。所以换号 / 重新登录之后，补一条命令即可：

```bash
# 前提：桌面客户端已登录新账号，并且正在运行（密钥只从进程内存里取）
python scripts/wbctl.py import
```

- **客户端侧什么都不用改** —— baseURL 和模型清单与账号无关，dsh 等客户端也不用重启。
- 同一个 uid 重新登录 = 原位替换 token，积分 / 签到 / 档位记录都保留。
- **换了 uid 是新增一条，旧账号不会自动删除。** 旧号 token 失效后会持续失败，建议清掉：
  面板上删，或 `rm ~/.wb-proxy/hub/accounts/<旧uid>.json`。
  留着也并非没有好处 —— 多号额度叠加、轮询分摊，限流的模型会自动跳过它。

> 限流（`429 / code 6004 usage exceeds frequency limit`）是**账号级**的，换号立刻绕过。
> 但 `gpt-*`、`gemini-*`、`grok-4.7` 的 `503 no usable account` 是**账号档位**问题
> （非 PRO 拿不到），换号解决不了。

**密钥只存在内存里、不落盘。** 网关进程重启过（含关机重开）之后，`import` 必须先让
桌面客户端运行起来 —— 它是密钥的唯一来源。这不影响已有账号的日常对话：续期用的是
`refreshToken`，不需要那个静态密钥。

---

## 排查顺序

按这个顺序看，能命中 95% 的问题：

```bash
python scripts/wbctl.py status          # 网关活着吗？账号几个？出口哪个？
python scripts/wbctl.py logs --lines 60 # 上游到底报了什么
python scripts/wbctl.py probe --models <出问题的模型>
```

| 现象 | 原因 | 处理 |
|---|---|---|
| `import` 报「回收密钥失败」 | 桌面客户端没运行 / 没登录 | 打开 WorkBuddy AI 并登录，再跑 |
| 对话 503 `no usable account for realm` | 该出口账号全被冷却/禁用，或模型是 PRO 专属 | `probe` 换模型；或切出口 |
| 对话 400「只在国内版提供」 | 模型与出口不匹配 | 切出口，或换等价模型 |
| 请求 502 / 连不上 | 系统代理劫持了回环地址 | 用脚本内的请求方式，或 `no_proxy=127.0.0.1` |
| 客户端里还是旧模型 | 客户端没重启 / 配置被它自己覆盖 | 重启客户端；ZCode 必须先退出再 `apply` |
| `start` 说端口被占用 | 8788 被上一次进程占着 | `stop`，或 `--port 8789` |

更细的诊断见 `reference/troubleshooting.md`，链路原理见 `reference/architecture.md`。

---

## 安全

- `~/.wb-proxy/hub/accounts/*.json` 里是**明文 accessToken / refreshToken**。
  别放同步盘、别提交进任何仓库（`.gitignore` 已排除，但 `~/.wb-proxy` 本身不在本仓库里）。
- 网关默认只监听 `127.0.0.1`。要开给局域网（`--lan`）必须设 api_key。
- 共享给别人 = 把你的 WorkBuddy 额度给别人用，且任何拿到 token 的人都能冒充你的账号。

---

## 卸载

```bash
python scripts/wbctl.py uninstall-task   # 装过计划任务的话先撤，否则登录时还会被拉起
python scripts/wbctl.py stop
rm -rf ~/.wb-proxy            # 运行目录 + 账号 + 日志
```

客户端配置里的 `wb-proxy` provider 条目需手动删（或改回原来的 provider）。

---

## 致谢

网关本体：**workbuddy2api-hub**（MIT），作者 ardeyouxipianyi。
本仓库只做封装与运维，许可证与归属见 `THIRD_PARTY_NOTICES.md`。
