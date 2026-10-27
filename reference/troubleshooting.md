# 排查手册

## 先跑这三条

```bash
python scripts/wbctl.py status            # 网关活着吗 / 账号几个 / 出口哪个
python scripts/wbctl.py logs --lines 80   # 上游到底报了什么
python scripts/wbctl.py probe --models <出问题的模型>
```

90% 的问题在这三条里就能看出来。

---

## 启动阶段

| 现象 | 原因 | 处理 |
|---|---|---|
| `端口 8788 被别的程序占用` | 上一次的网关还在，或别的服务占了 | `wbctl.py stop`；或 `--port 8789` |
| `网关 90 秒内没起来` | 首启动在初始化目录/拉模型目录；或 Python 太旧 | 看 `~/.wb-proxy/logs/gateway.log`；确认 Python ≥ 3.9 |
| 起完 `账号 0/0` | 还没导号 | 跑 `import` |
| 双击 `bin\wb-proxy.cmd` 一闪而过 | 脚本报错退出 | 打开 cmd 手动执行 `python scripts\wbctl.py start` 看输出 |

## 导入账号

| 现象 | 原因 | 处理 |
|---|---|---|
| `回收密钥失败` / `keyblob not found` | WorkBuddy 桌面客户端没开、没登录，或版本换了密钥槽 | 打开客户端 → 登录 → 确保它**正在运行** → 重跑 `import` |
| 导入 0 个账号 | 客户端从没登录过，或凭据文件已被清理 | 在客户端里正常发一条消息，让它落盘凭据，再 `import` |
| 导入成功但对话 401 | token 过期、refresh 失败 | `status --refresh`；仍失败就重登客户端再 `import` |
| 积分显示 `-` | 只导了号没刷积分 | `status --refresh`（或面板点「刷新积分」） |

## 对话阶段

| 现象 | 含义 | 处理 |
|---|---|---|
| `503 no usable account for realm 'intl': all are disabled, cooling down, exhausted` | 该出口账号全被冷却/限额/禁用，或该模型只给 PRO | 换个账号出口，或 `probe` 找能用模型；PRO 专属模型（`gpt-*`/`gemini-*`）在免费号上必然如此 |
| `400 模型 X 只在国内版提供，但…绑定的是国际版出口` | 模型与出口不匹配 | 切出口（见 SKILL.md「出口」一节），或换等价模型 |
| `400 模型 X 只在国际版提供…` | 反向不匹配，同上 | 同上 |
| `402 Insufficient Balance` | 这不是本网关的错误 —— 这是**DeepSeek 官方 API** 的余额不足 | 付费充值，或改用网关里的 `deepseek-v4.1-flash` |
| `11128 request illegal / failed channel verification` | 上游认为请求不像官方客户端 | 别自己拼请求；确认走的是网关，且没被中间代理改写 UA |
| 请求 502 / 连接被拒 | 系统代理劫持了 `127.0.0.1` | 脚本内已用 `ProxyHandler({})`；自己写脚本也要这样，或设 `no_proxy=127.0.0.1` |
| 流式返回但内容为空 | 上游偶发截断 | 重试；持续出现时 `probe` 换模型 |
| 首字很慢（5–20s） | 上游排队，尤其在非 PRO 账号 | 正常现象；`probe` 输出的 `credit=0` 模型通常更快 |

## 客户端侧

| 现象 | 原因 | 处理 |
|---|---|---|
| `apply` 成功但客户端里看不到 provider | 配置只在启动时读 | **重启客户端**（dsh 的新插件条目必须重载） |
| dsh 里旧会话仍报旧错误 | 会话把 provider 固化在 `~/.dsh/storages/session_projcache/sessions/*.json` | 新建会话 |
| ZCode 配置被改回去 | ZCode 运行时会重写自己的配置文件 | 先退出 ZCode 再 `apply` |
| Codex / Claude Code 报 401 | 网关设了 `--api-key` 但客户端没带 | 让客户端带上同一个 key，或去掉 api_key |
| 客户端模型列表里一半调不通 | 目录里 24 个模型不等于 24 个能用 | `probe --apply-ok` 只写可用清单 |

---

## 手工验证（不依赖脚本）

```bash
# 健康
curl --noproxy 127.0.0.1 http://127.0.0.1:8788/health

# 模型目录
curl --noproxy 127.0.0.1 http://127.0.0.1:8788/v1/models

# 真发一句
curl --noproxy 127.0.0.1 http://127.0.0.1:8788/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4.1-flash","messages":[{"role":"user","content":"ping"}],"max_tokens":16}'
```

`--noproxy 127.0.0.1` 很关键：Windows 上系统代理常把回环地址也代理掉，症状是 502。

## 看请求落到了哪个账号

```bash
tail -5 ~/.wb-proxy/hub/usage/usage.jsonl
```

每行带 `model` / `account` / `realm` / `credit` / `total_tokens`。
判定「这次对话用的是国际版还是国内版」以这一列为准 —— `/health` 的 `uid` 字段只是展示用的代表账号，不是本次请求的账号。
