# wb-proxy

**把 WorkBuddy AI 的模型额度，变成本机一个标准 OpenAI 兼容接口。**

```
你的客户端                    wb-proxy                        WorkBuddy
┌──────────────┐        ┌────────────────────┐        ┌──────────────────────┐
│ dsh          │        │ 127.0.0.1:8788/v1  │        │ www.workbuddy.ai     │
│ Claude Code  │ ─────► │  · 多账号轮询/冷却  │ ─────► │ (国际版, realm=intl)  │
│ Codex        │  OpenAI│  · 积分/限额/日活   │  签名   │ copilot.tencent.com  │
│ OpenCode     │  协议  │  · 看板 + 日志      │  指纹   │ (国内版, realm=cn)    │
└──────────────┘        └────────────────────┘        └──────────────────────┘
```

本仓库是一个 **WorkBuddy 技能（skill）**，把开源网关
[workbuddy2api-hub](https://github.com/ardeyouxipianyi/workbuddy2api-hub) 打包成
「一条命令部署 + 常驻 + 导号 + 写客户端配置 + 验收」的完整形态。

## 为什么需要它

WorkBuddy AI 桌面客户端里有一批模型（`deepseek-v4.1-flash`、`hy3`、`glm-5.3`、`kimi-k3`…），
走的是它自己的登录态与渠道校验，普通 OpenAI SDK 调不通。这个网关做了两件事：

1. **拿到账号凭据**：桌面客户端的凭据在磁盘上是 AES-256-GCM 信封，主密钥在原生模块里；
   网关从**运行中进程的内存**里扫出候选，用 `sha256(候选)[:16]` 比对 keyblob 的
   `protectorKeyId` 命中 —— 所以**必须先登录并运行桌面客户端**。
2. **过上游渠道校验**：上游校验的不是单个签名，而是一整套客户端指纹（UA、机器码、
   会话标识、出口域名）。网关按 uid 派生稳定机器码，就能稳定通过。

## 快速开始

```bash
git clone https://github.com/icdi24/wb-proxy.git
cd wb-proxy

python scripts/wbctl.py doctor      # 自检
python scripts/wbctl.py start       # 起网关（后台，127.0.0.1:8788）
python scripts/wbctl.py import      # 从桌面客户端导号 + 刷积分
python scripts/wbctl.py selftest    # 真发一次对话验收
```

然后把 `http://127.0.0.1:8788/v1` 填进任意 OpenAI 兼容客户端；
或者让脚本代劳：

```bash
python scripts/wbctl.py apply --list          # 看本机装了哪些客户端
python scripts/wbctl.py apply --client dsh    # dsh / claude-code / codex / opencode / crush / zcode
python scripts/wbctl.py probe --apply-ok      # 探出真能用的模型，只把可用的写进配置
```

Windows 也可以直接双击 `bin\wb-proxy.cmd`。

## 它解决过的两个坑

| 坑 | 表现 | 本项目的处理 |
|---|---|---|
| 目录里 24 个模型，一半调不通 | `gpt-*` / `gemini-*` 在非 PRO 账号上 503 | `probe` 逐个打真实请求，`--apply-ok` 只写能用的 |
| 出口与模型不匹配 | `deepseek-v4-pro` 走国际版直接 400 | 按模型判定出口，报错里直接说明该切哪边 |

## 文档

- `SKILL.md` —— 技能说明：命令参考、目录约定、排查顺序
- `reference/architecture.md` —— 请求链路、密钥回收原理、出口判定
- `reference/troubleshooting.md` —— 错误码与处理表
- `upstream/README.md` —— 上游网关的完整文档（模型清单、面板、调度、限额…）

## 安全边界

- 网关默认只监听 `127.0.0.1`。
- `~/.wb-proxy/hub/accounts/*.json` 是**明文 token**，别放同步盘、别提交。
- 分享网关等于分享你的 WorkBuddy 额度。

## 许可证

本仓库的封装代码 MIT。网关本体为 **workbuddy2api-hub**（MIT），
来源、版本与归属声明见 `THIRD_PARTY_NOTICES.md`。
