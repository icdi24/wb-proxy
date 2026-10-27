# 第三方组件与归属

## workbuddy2api-hub

- 来源：https://github.com/ardeyouxipianyi/workbuddy2api-hub
- 作者：ardeyouxipianyi
- 许可证：MIT
- 本项目中的位置：`upstream/`（原样 vendored，未修改逻辑）
- 拉取时间：2026-10-26（上游压缩包快照，无 git 元数据）
- 包含文件：根目录 `*.py`（网关本体与模块）、`dashboard.html`（看板）、
  `catalog/`、`pricing/`、`README.md`、`LICENSE`

上游 `LICENSE` 原文保留在 `upstream/LICENSE`，版权声明如下：

```
MIT License

Copyright (c) 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, ...
```

### 未包含的内容（有意排除）

| 排除项 | 原因 |
|---|---|
| `accounts/` | 含使用者本人的**明文 token**，绝不入库 |
| `usage/`、`*.log` | 运行时数据与日志，可能含账号 uid 与请求摘要 |
| `tests/` | 上游测试集（3.8 MB），与部署无关 |
| `__pycache__/` | 编译缓存 |
| `docs/`、`ops/`、`wrt/`、`release/` | 上游内部运维资料，非运行所需 |

### 更新上游

```bash
# 重新拉一份上游快照覆盖 upstream/，然后同步到运行目录
python scripts/wbctl.py upgrade
```

---

## 免责声明

本项目仅用于在**本机**复用你已登录的 WorkBuddy 账号额度，仅供个人学习与自用。
请遵守 WorkBuddy AI / 腾讯的服务条款；因转售、共享或超量调用导致的风险由使用者自负。
