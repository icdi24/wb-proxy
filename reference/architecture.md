# 链路与原理

## 一、请求怎么走

```
客户端 (dsh / Claude Code / …)
  │  POST http://127.0.0.1:8788/v1/chat/completions      OpenAI 协议
  ▼
wb_proxy.py（本机，只监听 127.0.0.1）
  │  ① 按 model 判出口 → 从账号池挑一个账号（轮询 + 冷却 + 会话亲和）
  │  ② 把 OpenAI 请求体转成上游格式，注入必需的系统提示
  │  ③ 套上「官方客户端指纹」：UA、X-Domain、X-Product、X-User-Id…
  ▼
上游聊天端点  <realm.chat_upstream> + /v2/chat/completions
  intl → https://www.workbuddy.ai
  cn   → https://copilot.tencent.com
  │  Authorization: Bearer <账号 accessToken>
  ▼
WorkBuddy 后端 ──► 模型（deepseek / hy3 / glm / kimi / gpt / gemini…）
```

计费、限额、日活、积分包都在网关侧记账（`~/.wb-proxy/hub/usage/usage.jsonl`），
不改变上游的任何额度规则。

## 二、为什么磁盘上的凭据用不了

WorkBuddy 桌面客户端的凭据是 `$wbEncrypted` 信封：**AES-256-GCM** 加密，
主密钥不在文件里，而在原生模块（`electron.workbuddyStorage`）运行时内存中，
并由客户端配置目录下的 `keyblob` 里的 `protectorKeyId` 索引。

所以静态取证这条路是死的。网关用的办法：

1. `OpenProcess(PROCESS_VM_READ)` 打开运行中的客户端进程；
2. 扫描内存里的候选字节串作为 AES 密钥；
3. 对每个候选算 `sha256(候选).hex()[:16]`，与 `keyblob` 里 `static-v1` 槽的
   `protectorKeyId` 比对，命中即确认；
4. 用这把密钥解开 `.info` 里的 `$wbEncrypted` 字段，得到 accessToken / refreshToken。

命中之后密钥**只留在网关进程内存**里，不落盘；网关重启后重新找一次。
这就是 `import` 必须在「客户端已登录且正在运行」时执行的原因。

> Linux / macOS 上无法这样扫进程内存，上游提供了 `keyText` 手工导入解码密钥的通道。

## 三、为什么上游会拦，又怎么过

早期尝试直接拿 token 打上游，会得到：

```
11128 request illegal / failed channel verification,
possibly because it was not sent from an official client
```

它校验的不是单个签名，而是**一整套客户端指纹**：

| 维度 | 值 |
|---|---|
| User-Agent | 三段式桌面端 UA，如 `WorkBuddy/5.8.4 workbuddy-ai/5.8.4 CLI/2.161.0` |
| X-Domain / X-Product | 账号自己的 domain、`workbuddy` |
| X-User-Id | 账号 uid |
| 机器码 | 按 uid 派生，跨请求稳定 |
| 会话标识 | 每次会话一致，不能乱跳 |

网关把这几项都固定下来，请求看起来就像官方客户端发出来的。单靠换 UA 不够，
**机器码必须稳定且与 uid 匹配** —— 这是最容易漏的一环。

## 四、出口（realm）

两个出口是两套独立的账号体系，token 不通用：

| realm | 上游 | 说明 |
|---|---|---|
| `intl` | `www.workbuddy.ai` | 国际版 |
| `cn` | `copilot.tencent.com` | 国内版 |

模型 → 出口的判定（`detect_model_realm`）：

- `gpt-*`、`gemini-*`、`grok-4.7` → **intl 强制**
- `deepseek-v4-pro`、`minimax-*`、`kimi-k3-1` 等 → **cn 强制**
- 其余（`deepseek-v4.1-flash`、`hy3`、`glm-5.3`、`kimi-k3`、`glm-5.2`…）→ **跟随当前出口**

当前出口默认 `intl`，由 `~/.wb-proxy/hub/accounts/active_realm.json` 持久化。
出口不匹配会被网关提前拦下并给一句人话，不会把请求打到上游才失败。

## 五、面板接口（脚本用到的）

登录：`POST /panel/login {"password": "admin"}` → `{"token": "..."}`，
之后所有面板调用带 `X-Panel-Token`。

| 接口 | 用途 |
|---|---|
| `GET /health` | 存活、出口、账号数 |
| `GET /accounts?realm=all` | 账号列表（含积分、权重、冷却） |
| `POST /accounts/import/desktop {"recoverKey": true}` | 从进程内存回收 at-rest 密钥 |
| `POST /accounts/import/desktop {"all": true}` | 导入扫描到的全部账号 |
| `POST /accounts/refresh` | 刷新 token |
| `GET /accounts/credits/detail?uid=<uid>&refresh=1` | 拉积分明细（可强制刷新） |
| `GET /agents` | 客户端清单与检测状态 |
| `POST /agents/apply {"client","base_url","model","models"}` | 写客户端配置 |
| `GET /v1/models` | OpenAI 兼容模型目录 |
| `POST /v1/chat/completions` | OpenAI 兼容对话 |

## 六、运行时文件

```
~/.wb-proxy/
├── hub/                     ← upstream/ 的副本（代码）
│   ├── wb_proxy.py
│   ├── accounts/            ← 账号凭据（明文 token，勿同步）
│   │   ├── <uid>.json
│   │   ├── active_realm.json
│   │   └── settings.json
│   ├── usage/usage.jsonl    ← 每次请求一行：模型、token、耗时、积分、账号
│   └── catalog/             ← 上游模型目录缓存（会自行刷新）
├── logs/gateway.log
├── gateway.pid
└── config.json              ← 端口 / api_key / 面板密码
```

`usage.jsonl` 是审计「这次请求到底扣了哪个账号」的唯一权威来源 ——
判定「绑的是国际版还是国内版」时，看这一列比看 /health 准确。
