# Gemini 账号管理面板与每日自动更新项目执行计划书 (Project Plan)

> **文档用途说明：**
> 本文档为《Gemini 多账号管理面板与自动更新系统 (Gemini Account Manager)》的完整技术规格与执行方案书。
> 本版本已完成架构审查，针对**自动更新缓存穿透、Cookie 刷新持久化回写、外部 API 代理兼容、公网安全鉴权、SQLite 数据库生命周期管理以及动态配额容错**等关键维护性问题进行了深度完善与防坑设计。
> **在新对话中，你可以直接把本文档发给 AI，并附上提示：“请根据本计划书，开始逐步创建各模块代码并实现系统”。AI 即可直接执行。**

---

## 1. 项目背景与目标

基于 [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API)（Python 库 `gemini-webapi`），构建一个**功能完备、开箱即用、单容器极简部署、具备生产级维护性的多账号管理与调用网关系统**。

### 核心功能需求：
1. **多账号状态监控 (Token 管理)**：集中管理多个 Gemini 账号（Cookie 凭据），实时展示账号健康状态（正常 / 失效 / 受限）、可用模型列表以及动态配额信息（Flash/Pro/Compute Quotas 动态解析，杜绝硬编码）。
2. **账号历史会话审计**：在前端查看每个账号最近在 Gemini 网页端产生的会话列表，并可展开查看会话详情（含用户 Prompt、AI 回复文本、思考链路与多媒体图片预览）。
3. **详细请求日志 (参考图 1、2、3)**：
   - 记录每一次调用的操作类型（`generate_image` / `chat_completion` 等）、Token 邮箱、状态（`已完成` / `失败`）、进度、状态码、结果摘要、耗时、时间。
   - **日志详情弹窗**：支持查看原始请求 JSON、结果摘要、生成结果（URL 预览/原图）、错误原因与错误响应 JSON、基本信息等。
   - 提供清空日志、实时刷新与分页筛选，后台支持 SQLite WAL 模式与日志自动按量滚动淘汰。
4. **灵活的前端自定义健康巡检周期 (系统配置)**：
   - 在前端界面自由设定**账号有效性检查周期**（如每 10 分钟、30 分钟、1 小时、自定义分钟数，或手动即刻全量巡检）。
   - 可配置是否自动禁用失效账号、请求超时时间、网络代理等，后台动态响应无需重启容器。
5. **在界面直接测试 (测试页面)**：提供测试面板，可指定账号或交由账号池自动调度，输入提示词进行对话或生图，快速验证凭据可用性并实时产生日志。
6. **对外标准化 API 网关代理 (OpenAI 格式兼容)**：
   - 提供 `/v1/chat/completions` 与 `/v1/images/generations` 统一入口，支持对接 NextChat、Cherry Studio、LobeChat、OneAPI 等第三方应用与自动化脚本。
   - 账号池支持轮询（Round-Robin）调度与故障自动故障转移（Failover），单账号配备并发信号量隔离保护，避免触发 Google 429。
7. **Cookie 动态自动刷新与持久化回写（防止重启失效）**：
   - 底层 SDK 自动轮换刷新 `__Secure-1PSIDTS` 时，后台主动监听并将新 Cookie 回写持久化到 `accounts.json`，确保容器随 Watchtower 重启后账号不丢失、不失效。
8. **安全鉴权与敏感数据脱敏保护**：
   - 支持设置面板管理密码（`PANEL_PASSWORD`）与外部 API 访问密钥（`API_KEY`），杜绝公网部署时凭据被扫描窃取；
   - `/api/accounts` 返回数据中对敏感 Cookie 自动脱敏打码展示。
9. **多格式 Cookie 兼容导入与智能解析 (参考 cli.py)**：
   - 支持手动单项输入，更支持直接粘贴浏览器插件（Cookie-Editor、EditThisCookie）导出的 JSON 数组或键值对象；
   - 后台自动识别并提取 `__Secure-1PSID`、`__Secure-1PSIDTS` 及其过期时间 `expirationDate`，大幅降低添加账号的操作门槛。
10. **国内网络友好型媒体反向代理预览 (`/api/proxy/image`)**：
    - 由于 Google CDN (`flow-content.google.com` / `lh3.googleusercontent.com`) 在国内网络环境下不可直连，日志详情与历史会话中的图片预览支持通过后端网络代理中转流式输出，杜绝前端大面积“图片破损”。
11. **每日全自动真实无缝更新**：
    - 通过 GitHub Actions 每天定时拉取原作者最新代码构建 Docker 镜像，通过 `CACHE_BUST` 参数彻底打破 Docker 构建缓存，推送到 GHCR；
    - 服务器端配合已有的 **Watchtower** 自动无感拉取重启，无需人工维护。
12. **单容器极简部署与非阻塞冷启动**：前后端一体化（FastAPI 静态托管现代化 SPA 前端），单一镜像构建，极低资源占用（内存 < 100MB）；账号池初始化放入后台异步预热，不阻塞 Web 服务的即刻就绪。

---

## 2. 推荐仓库目录树结构

新建独立仓库（例如命名为 `gemini-account-manager`），目录结构如下：

```text
gemini-account-manager/
├── .github/
│   └── workflows/
│       └── docker-build.yml       # GitHub Actions 每日凌晨自动构建（附带动态 CACHE_BUST 参数）
├── backend/
│   ├── __init__.py
│   ├── main.py                    # FastAPI 主程序：接口路由、静态资源托管、生命周期管理
│   ├── auth.py                    # 安全鉴权中心：面板密码验证、API Key 校验依赖、凭据脱敏处理
│   ├── account_manager.py         # 账号池管理器：加载/持久化 accounts.json、Cookie自动回写、并发锁与调度
│   ├── proxy_service.py           # 对外代理网关服务：OpenAI 兼容接口处理 (/v1/chat/completions, /v1/images)
│   ├── chat_service.py            # 会话与历史记录服务：获取会话列表、提取会话聊天详情
│   ├── log_service.py             # 请求日志管理：SQLite WAL 模式、日志持久化、分页查询与自动滚动清理
│   ├── settings_manager.py        # 系统配置管理：检查周期、密码、超时、日志清理策略持久化 settings.json
│   ├── health_checker.py          # 动态定时任务：账号可用性并发巡检、配额更新、日志自动归档
│   └── requirements.txt           # 基础依赖（fastapi, uvicorn, pydantic, httpx, aiosqlite 等）
├── frontend/                      # 现代化单页前端（由 FastAPI 静态托管，无需 Node 构建环境）
│   ├── index.html                 # 主界面：包含 Token管理 / 系统配置 / 请求日志 / 测试页面 四大 Tab 及登录 Modal
│   ├── style.css                  # 精美现代化样式（响应式、卡片、表格与弹窗）
│   └── app.js                     # 纯原生 JS 交互：Tab切换、表格渲染、日志详情弹窗、Token鉴权、测试调用
├── data/                          # 数据持久化目录（Docker 外部挂载点）
│   ├── accounts.json              # 账号配置持久化（含凭据、备注、代理等，运行时自动回写更新）
│   ├── settings.json              # 系统配置持久化（检查周期、安全密钥、日志清理策略等）
│   └── logs.db                    # 请求日志 SQLite 数据库（WAL 模式）
├── Dockerfile                     # 容器构建文件：支持 ARG 动态参数打破缓存，拉取上游最新源码
├── docker-compose.yml             # 服务器端一键启动配置（含密码及端口环境变量）
└── README.md                      # 仓库使用说明、环境变量速查与部署指南
```

---

## 3. 前端界面与排版设计 (UI Mockup)

顶部统一导航：
`[ 🔒 状态: 已认证 ]` | `Token 管理` | `系统配置` | `请求日志` | `测试页面` | `[ 🚪 退出 ]`

---

### 3.1 页面一：【请求日志】排版（完全对照参考图 1、2、3）

#### 列表主界面 (参考图 1)：
```text
+----------------------------------------------------------------------------------------------------+
|  [ Token 管理 ]   [ 系统配置 ]   [ 请求日志 (Active) ]   [ 测试页面 ]                  [ 🔒 安全退出 ]       |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|  请求日志                                                                   [ 🗑️ 清空 ]   [ 🔄 刷新 ]|
|                                                                                                    |
|  +-----------------------------------------------------------------------------------------------+ |
|  | 操作            Token邮箱              状态     进度    状态码  结果摘要                耗时(秒) 时间      操作 | |
|  +-----------------------------------------------------------------------------------------------+ |
|  | generate_image  未知                   [失败]   0%      503    没有可用的Token...      0.04    09/04 ... [详情] | |
|  | generate_image  未知                   [失败]   0%      503    没有可用的Token...      0.03    09/03 ... [详情] | |
|  | generate_image  woyaow800cac@gmail.com [已完成] 100%    200    图片结果已返回          47.20   08/31 ... [详情] | |
|  | generate_image  woyaow800cac@gmail.com [失败]   48%     500    生成失败: HTTP 404...   18.11   08/26 ... [详情] | |
|  | chat_completion woyaow800cac@gmail.com [已完成] 100%    200    文本回答生成成功        1.32    08/26 ... [详情] | |
|  +-----------------------------------------------------------------------------------------------+ |
|  显示第 1 到 10 条，共 58 条记录                                                    [ < 上一页 ] [ 下一页 > ] |
+----------------------------------------------------------------------------------------------------+
```

#### 日志详情弹窗 - 成功态 (参考图 2)：
```text
+-----------------------------------------------------------------------+
| 日志详情                                                     [ ✖ 关闭 ] |
+-----------------------------------------------------------------------+
| 请求数据                                                              |
| +-------------------------------------------------------------------+ |
| | {                                                                 | |
| |   "model": "gemini-2.5-flash",                                    | |
| |   "prompt": "一只猫",                                              | |
| |   "has_images": false                                             | |
| | }                                                                 | |
| +-------------------------------------------------------------------+ |
|                                                                       |
| 结果摘要                                                              |
| [ 绿色浅底卡片: 生成成功，已返回图片/文本结果 ]                        |
|                                                                       |
| 生成结果                                                              |
| +-------------------------------------------------------------------+ |
| | 主结果                                                            | |
| | URL: https://flow-content.google.com/image/...                    | |
| | [ 点击加载预览 ] (点击直接在弹窗内展示生成的图片)                  | |
| +-------------------------------------------------------------------+ |
|                                                                       |
| 2K/4K 资产信息                                                        |
| [ 无资产详情 ]                                                        |
|                                                                       |
| ▶ 完整响应 (大字段已截断折叠)                                         |
|                                                                       |
| 基本信息                                                              |
| 操作: generate_image                       状态: 已完成               |
| 状态码: 200                                耗时: 47.20秒              |
| 时间: 2026/8/31 05:53:58                   Token: woyaow800cac@gmail.com |
| 日志ID: 13                                 进度: 100%                 |
+-----------------------------------------------------------------------+
```

#### 日志详情弹窗 - 失败态 (参考图 3)：
```text
+-----------------------------------------------------------------------+
| 日志详情                                                     [ ✖ 关闭 ] |
+-----------------------------------------------------------------------+
| 请求数据                                                              |
| +-------------------------------------------------------------------+ |
| | {                                                                 | |
| |   "model": "imagen-3.0-generate-002",                             | |
| |   "prompt": "hi",                                                 | |
| |   "has_images": false                                             | |
| | }                                                                 | |
| +-------------------------------------------------------------------+ |
|                                                                       |
| 错误原因                                                              |
| [ 红色浅底卡片: 没有可用的Token进行图片生成。所有Token都处于禁用... ] |
|                                                                       |
| 错误响应                                                              |
| +-------------------------------------------------------------------+ |
| | {                                                                 | |
| |   "error": "没有可用的Token进行图片生成...",                       | |
| |   "status": "processing",                                         | |
| |   "token_select_ms": 13174                                        | |
| | }                                                                 | |
| +-------------------------------------------------------------------+ |
|                                                                       |
| 基本信息                                                              |
| 操作: generate_image                       状态: 失败                 |
| 状态码: 503                                耗时: 13.19秒              |
| 时间: 2026/9/1 03:42:27                    Token: 未知                |
| 日志ID: 14                                 进度: 0%                   |
+-----------------------------------------------------------------------+
```

---

### 3.2 页面二：【系统配置】排版

```text
+----------------------------------------------------------------------------------------------------+
|  [ Token 管理 ]   [ 系统配置 (Active) ]   [ 请求日志 ]   [ 测试页面 ]                  [ 🔒 安全退出 ]       |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|  ⚙️ 基础检测配置                                                                                    |
|  1. 账号有效性自动检查周期 (Health Check Interval):                                                 |
|     ( ) 每 10 分钟       (●) 每 30 分钟       ( ) 每 1 小时       ( ) 自定义: [  15  ] 分钟         |
|     说明: 后台常驻定时任务将按照此周期并发检验所有账号的 Cookie 有效性、刷新 Quotas 额度与可用模型。      |
|                                                                                                    |
|  2. 自动状态处理与容灾:                                                                            |
|     [✔] 当账号返回 UNAUTHENTICATED (Cookie失效) 时，自动将该账号标记为“禁用”并记录告警日志          |
|     [✔] 请求超时或遇到 Google 429 时自动重试其他可用账号 (账号池自动负载均衡与故障转移)            |
|                                                                                                    |
|  3. 网络与超时设置:                                                                                |
|     单个请求超时时间 (秒): [  30  ] 秒                                                             |
|     默认全局网络代理:      [ http://127.0.0.1:7890             ] (留空则使用系统环境代理)          |
|                                                                                                    |
|  🔒 安全与对外访问:                                                                                |
|     管理面板登录密码:      [ ••••••••••                        ] (留空则不开启前端登录认证)        |
|     API 访问密钥 (API Key):[ sk-gemini-manager-xxxx            ] (用于第三方客户端接入鉴权)        |
|                                                                                                    |
|  📦 数据库与日志维护策略:                                                                          |
|     最大保留日志条数:      [  20000  ] 条 (超出自动清理最早日志，防止数据库膨胀卡顿)               |
|     日志保留最长天数:      [  30     ] 天                                                          |
|                                                                                                    |
|  ⚡ 立即操作:                                                                                      |
|     [ ⚡ 立即执行一次全量账号健康巡检 ]                                                            |
|                                                                                                    |
|                                                     [ 💾 保存配置 (无需重启容器，动态立即生效) ]    |
+----------------------------------------------------------------------------------------------------+
```

---

### 3.3 页面三：【Token 管理】排版（动态配额展示与脱敏处理）

```text
+----------------------------------------------------------------------------------------------------+
|  [ Token 管理 (Active) ]   [ 系统配置 ]   [ 请求日志 ]   [ 测试页面 ]                  [ 🔒 安全退出 ]       |
+----------------------------------------------------------------------------------------------------+
|  [ 4 账号总数 ]  [ 🟢 3 正常运行 ]  [ 🔴 1 失效禁用 ]               [ ➕ 添加账号 ]   [ 🔄 刷新状态 ] |
|                                                                                                    |
|  +-------------------------------------+   +-------------------------------------+                 |
|  | 🟢 woyaow800cac@gmail.com   [正常]  |   | 🟢 dev_test@gmail.com       [正常]  |                 |
|  | 凭据: g.a8i...3x9a (已脱敏保护)     |   | 凭据: g.a8i...88cc (已脱敏保护)     |                 |
|  | 备注: 主力生图/日常开发              |   | 备注: 个人日常测试                  |                 |
|  +-------------------------------------+   +-------------------------------------+                 |
|  | 📊 动态配额 (Quotas 自动解析):      |   | 📊 动态配额 (Quotas 自动解析):      |                 |
|  |  Flash: [🟢 充裕] 剩余约 85%         |   |  Flash: [🟢 充裕] 100% 可用         |                 |
|  |  Pro:   [🟡 中等] 重置于 03:00      |   |  Pro:   [🟢 充裕] 100% 可用         |                 |
|  |  Compute Units: 正常 (无硬封锁)     |   |  Compute Units: 正常 (无硬封锁)     |                 |
|  |                                     |   |                                     |                 |
|  | 🏷️ 可用模型 (动态获取):              |   | 🏷️ 可用模型 (动态获取):              |                 |
|  |  [ gemini-2.5-flash ] [ 2.5-pro ]   |   |  [ gemini-2.5-flash ]               |                 |
|  +-------------------------------------+   +-------------------------------------+                 |
|  | [ 💬 查看会话历史 ] [ ✏️ 编辑 ] [ 🗑️ ] |   | [ 💬 查看会话历史 ] [ ✏️ 编辑 ] [ 🗑️ ] |                 |
|  +-------------------------------------+   +-------------------------------------+                 |
+----------------------------------------------------------------------------------------------------+
```

---

## 4. 后端核心架构与数据流

### 4.1 数据持久化设计 (`data/`)

1. **`data/accounts.json`**：存放多账号配置列表。
   ```json
   [
     {
       "id": "acc_1",
       "email": "woyaow800cac@gmail.com",
       "remark": "主力账号",
       "secure_1psid": "g.a8i...",
       "secure_1psidts": "sidts-...",
       "proxy": null,
       "is_enabled": true,
       "status": "ACTIVE",
       "last_checked_at": "2026-10-15T21:00:00Z",
       "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
       "quota_info": {}
     }
   ]
   ```
2. **`data/settings.json`**：存放系统与安全配置：
   ```json
   {
     "health_check_interval_minutes": 30,
     "auto_disable_expired": true,
     "failover_retry": true,
     "timeout_seconds": 30,
     "global_proxy": "",
     "panel_password": "",
     "api_key": "",
     "max_log_entries": 20000,
     "log_retention_days": 30
   }
   ```
3. **`data/logs.db` (SQLite + WAL 模式)**：
   - 在程序初始化连接时执行：`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`
   - 字段清单：
     `id` (INTEGER PRIMARY KEY), `operation` (TEXT), `token_email` (TEXT), `status` (TEXT), `progress` (TEXT), `status_code` (INTEGER), `summary` (TEXT), `duration` (REAL), `created_at` (TEXT), `request_data` (TEXT), `response_data` (TEXT), `error_reason` (TEXT), `error_response` (TEXT), `result_url` (TEXT)。

---

### 4.2 关键机制实现要求（防坑关键）

#### 1. Cookie 动态刷新与持久化回写机制（核心防雪崩）
- **现象**：`gemini-webapi` 的 `GeminiClient` 在后台自动刷新 `__Secure-1PSIDTS`。
- **机制设计**：
  - `account_manager.py` 维护每个活跃账号的 `GeminiClient` 实例。
  - **凭据提取标准实现（参考 `cli.py` 的 `_persist_cookies`）**：遍历 `client._client.cookies.jar`（或底层 httpx session cookies），抓取最新的 `__Secure-1PSID` 与 `__Secure-1PSIDTS`。
  - 在每次调用完成或定时健康巡检时，若检测到凭据值与 `data/accounts.json` 中不一致，立即执行原子写入持久化。
  - **收益**：即便夜间 Watchtower 自动拉取最新 Docker 镜像重启容器，读取的仍然是最新的有效 Cookie，绝对不会发生“容器重启全军失效”的灾难。

#### 2. 多格式 Cookie 导入与智能解析器（参考 `cli.py` 的 `_load_cookies_with_meta`）
- 用户添加账号时，往往从浏览器插件导出各种格式的 Cookie。系统需提供统一解析函数：
  - **格式 1**：扁平键值对 `{"__Secure-1PSID": "...", "__Secure-1PSIDTS": "..."}`
  - **格式 2**：包装字典 `{"cookies": {"__Secure-1PSID": "..."}}`
  - **格式 3**：浏览器插件导出数组 `[{"name": "__Secure-1PSID", "value": "...", "expirationDate": 179...}, ...]`
- 后端自动清洗并提取出关键凭据及过期时间（`expirationDate`），免除用户人工手动挑选键值的痛苦。

#### 3. 对外标准化 API 代理与账号池调度 (`proxy_service.py`)
- 提供标准 `/v1/chat/completions` 与 `/v1/images/generations`。
- **调度策略**：
  - 维护账号实例的可用队列，使用轮询（Round-Robin）调度健康账号。
  - 单账号增加并发限制（通过 `asyncio.Semaphore(2)`），避免瞬时并发导致 Google 封控。
  - 若命中 `AccountStatusError` 或 `APIError(429)`，触发自动故障转移（Failover），调度下一个健康账号重试，并将失败事件记录到日志。
  - 每次代理请求无论成功失败，异步向 `log_service` 写入完整结构化审计记录。

#### 4. 媒体反向代理预览与非阻塞冷启动
- **媒体反代 (`/api/proxy/image`)**：由于国内无法直连 Google CDN (`flow-content.google.com`)，前端预览时通过该接口由后端代下并流式返回，彻底解决预览图破损。
- **非阻塞冷启动**：FastAPI 服务启动时优先挂载路由与前端页面，账号连通性检测与客户端预热移至后台 `asyncio.create_task` 执行，避免因网络慢卡死容器启动。

#### 5. 动态健康巡检器 (`health_checker.py`)
- 使用 `asyncio.Event` 结合 `asyncio.sleep`。
- 当用户在前端修改巡检间隔时，通过事件唤醒，无需重启进程即刻按新周期调度。
- 巡检时使用 `asyncio.Semaphore(5)` 限制检测并发度，防止几十个账号同时握手触发本机端口占用或代理断流。
- 巡检结束自动触发一次轻量日志清理：`DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT ?)`。

#### 6. 安全防护与脱敏 (`auth.py`)
- 面板配置了 `panel_password` 时，前端所有 `/api/*` 请求（除登录外）需携带 `Authorization: Bearer <token>` 或 Cookie。
- 对外网关 `/v1/*` 若配置了 `api_key`，校验 Header `Authorization: Bearer <api_key>`。
- `/api/accounts` 接口对凭据做脱敏：`secure_1psid` 仅显示前 6 位与后 4 位，其余转为 `***`。

---

### 4.3 核心 API 路由清单：

| 模块 | 请求方法 | 路由地址 | 功能说明 |
| :--- | :--- | :--- | :--- |
| **认证** | `POST` | `/api/auth/login` | 管理员登录校验，返回访问令牌 |
| **认证** | `GET` | `/api/auth/status` | 获取当前安全配置状态（是否开启密码防护） |
| **账号** | `GET` | `/api/accounts` | 获取所有账号状态、动态配额、脱敏凭据、模型列表 |
| **账号** | `POST` | `/api/accounts` | 新增账号（支持单项输入或直接粘贴浏览器多格式 Cookie JSON） |
| **账号** | `PUT` | `/api/accounts/{id}` | 更新账号凭据或备注 |
| **账号** | `DELETE`| `/api/accounts/{id}` | 删除指定账号并释放客户端连接 |
| **会话** | `GET` | `/api/accounts/{id}/chats` | 获取网页端历史会话列表 |
| **会话** | `GET` | `/api/accounts/{id}/chats/{cid}` | 读取会话完整对话历史详情 |
| **配置** | `GET` | `/api/settings` | 读取当前系统配置（周期、安全、日志保留） |
| **配置** | `POST` | `/api/settings` | 更新系统配置并动态重置定时器 |
| **配置** | `POST` | `/api/settings/check-now` | 立即触发一次全量账号健康巡检 |
| **日志** | `GET` | `/api/logs` | 分页查询请求日志列表（支持状态/操作类型过滤） |
| **日志** | `GET` | `/api/logs/{id}` | 获取单条日志详细数据（供详情弹窗展示） |
| **日志** | `DELETE`| `/api/logs` | 一键清空所有日志记录 |
| **测试** | `POST` | `/api/test/generate` | 在测试页面触发对话或图片生成 |
| **媒体** | `GET` | `/api/proxy/image` | **反向代理中转 Google 图片流（解决国内环境图片裂开破损）** |
| **网关** | `POST` | `/v1/chat/completions` | **对外 OpenAI 兼容聊天接口（多账号自动池化代理）** |
| **网关** | `POST` | `/v1/images/generations` | **对外 OpenAI 兼容生图接口** |

---

## 5. 自动化构建与更新机制 (`.github/workflows/` + `Dockerfile`)

### 5.1 `Dockerfile`（支持动态缓存穿透与环境自适应）
```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. 允许外部传入动态参数与自定义上游仓库
ARG UPSTREAM_REPO=https://github.com/HanaokaYuzu/Gemini-API.git
ARG CACHE_BUST=1

# 2. 动态安装原作者最新代码（通过 CACHE_BUST 彻底打破 Docker 缓存层，确保每次构建都拉取最新 master）
RUN echo "Building with cache bust: ${CACHE_BUST}" && \
    pip install --no-cache-dir git+${UPSTREAM_REPO}

# 3. 安装管理面板后端依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 复制代码与前端静态文件
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data
ENV PORT=8000

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
```

### 5.2 GitHub Actions 每日自动构建 (`.github/workflows/docker-build.yml`)
- 设置 `cron: '0 19 * * *'`（北京时间每日凌晨 03:00 自动触发），同时支持 `workflow_dispatch` 手动触发。
- 在构建步骤中，动态注入当前时间戳作为 `CACHE_BUST` 参数，确保无论上游何时提交更新，每天清晨的镜像必定包含最新源码：
  ```yaml
  - name: Build and push Docker image
    uses: docker/build-push-action@v5
    with:
      context: .
      push: true
      tags: |
        ghcr.io/${{ github.repository_owner }}/gemini-account-manager:latest
      build-args: |
        CACHE_BUST=${{ github.run_id }}
  ```

### 5.3 服务器端 Watchtower 配合部署 (`docker-compose.yml`)
在服务器端，配合现有的 Watchtower 容器，使用如下配置：
```yaml
services:
  gemini-manager:
    image: ghcr.io/<你的GitHub用户名>/gemini-account-manager:latest
    container_name: gemini-manager
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
      - PANEL_PASSWORD=your_secure_password # 可选，面板管理密码
      - API_KEY=sk-gemini-gateway-secret    # 可选，对外 API 代理密钥
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
```
当 Watchtower 轮询到新镜像发布后，自动拉取并重启容器；由于容器内的 `account_manager` 已经将最新 Cookie 实时回写到了挂载卷 `./data/accounts.json`，重启过程平滑无感，账号零失效。

---

## 6. 实施阶段分工指南（给新聊天框 AI 的执行指令）

在新的会话中，请将此步骤指导作为执行依据：

```markdown
请按照以下四个阶段依次实现《Gemini 账号管理面板与自动更新系统》：

- [x] 阶段 1：构建数据层与后端核心 API
  - [x] 编写 backend/requirements.txt（引入 fastapi, uvicorn, aiosqlite, httpx 等）
  - [x] 编写 backend/settings_manager.py 实现系统配置（含密码、API Key、巡检周期、日志淘汰策略）读取与更新
  - [x] 编写 backend/auth.py 实现面板鉴权依赖、API Key 校验以及敏感凭据脱敏函数
  - [x] 编写 backend/log_service.py 基于 SQLite WAL 模式实现日志写入、分页查询、清空与自动滚动淘汰
  - [x] 编写 backend/account_manager.py 实现多格式 Cookie 智能清洗导入（参考 cli.py）、多账号加载、健康检测、并发锁保护、Cookie 轮换自动回写持久化
  - [x] 编写 backend/health_checker.py 实现可动态唤醒重置的巡检后台任务，并发限制与自动维护
  - [x] 编写 backend/chat_service.py 封装会话列表与对话详情提取
  - [x] 编写 backend/proxy_service.py 实现对外标准化 /v1/chat/completions 与 /v1/images/generations 代理分发
  - [x] 编写 backend/main.py 聚合所有路由、实现 /api/proxy/image 图片反代、启动后台异步预热任务并挂载静态前端

- [x] 阶段 2：构建现代化前端单页控制台
  - [x] 编写 frontend/index.html 实现四大 Tab 布局（Token管理、系统配置、请求日志、测试页面）与登录认证模态框
  - [x] 编写 frontend/style.css 实现现代化卡片、表格与弹窗样式（严格对照参考图1/2/3，精致现代感排版）
  - [x] 编写 frontend/app.js 实现各 Tab 切换、日志列表与详情 Modal、检查周期配置实时保存、测试调用及 Token 登录拦截

- [x] 阶段 3：构建与部署配置
  - [x] 编写 Dockerfile（引入 ARG UPSTREAM_REPO 和 ARG CACHE_BUST 彻底打破层缓存，自适应环境变量）
  - [x] 编写 docker-compose.yml（包含挂载卷、环境参数模板、Watchtower 标签）
  - [x] 创建初始 data/ 目录及模板 accounts.json, settings.json

- [x] 阶段 4：配置 GitHub Actions 每日自动化流水线
  - [x] 编写 .github/workflows/docker-build.yml，构建时传入动态 CACHE_BUST，自动推送到 GHCR

---

## 7. 验收标准与验证方案 (Acceptance Criteria)

为确保系统开发质量与运维健壮性，各模块必须通过以下量化与功能验收标准：

### 7.1 凭据解析与账号管理验收
- [x] **多格式 Cookie 解析**：
  - 支持解析纯键值 JSON 对象（`{"__Secure-1PSID": "...", "__Secure-1PSIDTS": "..."}`）。
  - 支持解析包装字典格式（`{"cookies": {...}}`）。
  - 支持解析 Cookie-Editor / EditThisCookie 导出的浏览器数组（自动提取 name 为 `__Secure-1PSID` 与 `__Secure-1PSIDTS` 的项，并解析 `expirationDate`）。
- [x] **敏感信息脱敏**：
  - `/api/accounts` 接口返回的 `secure_1psid` 必须脱敏（仅保留前 6 位和后 4 位，中间使用 `***` 遮蔽），不能明文泄露至前端 DOM。
- [x] **Cookie 动态刷新与持久化回写**：
  - 当底层 `GeminiClient` 轮换更新 `__Secure-1PSIDTS` 时，后台能检测到并安全原子更新持久化至 `data/accounts.json`，保留已有备注与代理配置。
- [x] **账号调度与并发保护**：
  - 单账号具备并发信号量控制（默认最大并发 2），防止并发击穿触发 Google 封控。
  - 支持多账号轮询（Round-Robin）调度，当某账号返回未授权或 429 时，能够自动 Failover 切换到下一个可用账号。

### 7.2 安全与权限鉴权验收
- [x] **管理面板密码保护**：
  - 未配置 `PANEL_PASSWORD` 时，面板可直接免密访问。
  - 配置 `PANEL_PASSWORD` 时，未登录访问受保护的 `/api/*` 接口返回 401 状态码，通过 `/api/auth/login` 校验后下发 Bearer Token，并在请求头中携带校验通过。
- [x] **对外 API 网关密钥鉴权**：
  - 未配置 `API_KEY` 时，`/v1/*` 允许直接调用。
  - 配置 `API_KEY` 时，若未提供或提供错误的 `Authorization: Bearer <key>`，统一返回 401 拦截。

### 7.3 请求日志与 SQLite 数据库验收
- [x] **SQLite WAL 模式与并发性能**：
  - 数据库初始化时启用 `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`，确保高频读写无锁死报错。
- [x] **日志字段完整性**：
  - 无论对话还是生图、成功还是失败，均完整写入操作类型、Token 邮箱、状态（`已完成` / `失败`）、进度（0% / 100%）、状态码、结果摘要、耗时（秒）、时间、请求 JSON、响应/错误详情及图片结果 URL。
- [x] **查询与管理功能**：
  - 支持分页（页码、每页条数）、状态过滤（全部/已完成/失败）、操作类型过滤。
  - 单条日志可通过 `/api/logs/{id}` 获取详细 JSON 数据。
  - 提供一键清空日志接口 `/api/logs` (DELETE)。
- [x] **自动滚动清理**：
  - 支持根据系统配置的 `max_log_entries`（如 20000 条）和 `log_retention_days`（如 30 天）在后台巡检时自动清理过期或溢出日志，防止磁盘膨胀。

### 7.4 动态健康巡检与配置系统验收
- [x] **动态任务调度**：
  - 修改 `health_check_interval_minutes` 并保存后，后台定时任务立即按新周期重置等待，无需重启服务或容器。
  - 点击“立即执行一次全量账号健康巡检”按钮，可非阻塞即刻触发全量账号检测。
- [x] **健康巡检并发控制**：
  - 巡检过程中使用信号量限制并发检查数（如最大并发 5），避免瞬间大量请求导致代理被封或端口耗尽。
- [x] **自动禁用失效账号**：
  - 开启 `auto_disable_expired` 时，检测到 Cookie 失效自动将账号置为禁用状态并记入审计日志。

### 7.5 对外兼容网关与反向代理验收
- [x] **OpenAI 兼容接口**：
  - `/v1/chat/completions`：支持标准 chat completion 请求体（`model`, `messages`），返回标准 chat completion 响应格式。
  - `/v1/images/generations`：支持标准 image generation 请求体（`prompt`, `n`, `size`），返回标准图片 URL / Base64 格式结果。
- [x] **媒体反代中转 (`/api/proxy/image`)**：
  - 针对 Google CDN 图片 URL，通过后端流式代理，解决国内前端预览图片防盗链或连通性失败问题。

### 7.6 前端控制台 UI/UX 验收
- [x] **单页开箱即用**：
  - 无需 Node/npm 构建，静态文件由 FastAPI 自动挂载托管，直接浏览器打开即可使用。
- [x] **页面布局与设计质感**：
  - 导航栏清晰划分四个主功能页：【Token 管理】、【系统配置】、【请求日志】、【测试页面】。
  - 视觉遵循现代仪表盘风格：雅致深色/精致浅色质感，卡片阴影、圆角、标签配色规范（绿色正常/浅底、红色失败、黄色警告）。
- [x] **请求日志与弹窗对照**：
  - 日志列表完整呈现：操作、Token邮箱、状态徽章、进度、状态码、结果摘要、耗时、时间、详情按钮。
  - 详情弹窗：
    - 成功态：展示格式化请求数据、绿色浅底摘要卡片、生成结果预览图（支持点击通过代理加载）、基本信息网格。
    - 失败态：展示格式化请求数据、红色浅底错误原因卡片、格式化错误响应 JSON、基本信息网格。
- [x] **在线测试面板**：
  - 可选择指定账号或自动池化轮询，支持输入文本或选择模型进行实时测试，调用过程实时输出并在日志中留存。

### 7.7 自动化构建与容器部署验收
- [x] **Dockerfile 与动态缓存穿透**：
  - `Dockerfile` 正确定义 `ARG CACHE_BUST` 与 `ARG UPSTREAM_REPO`，能够成功拉取上游最新源码并安装面板后端依赖。
- [x] **单容器极简运行**：
  - 启动后内存占用低于 150MB，冷启动时后台异步预热账号，Web 服务立即就绪（< 2 秒）。
- [x] **数据持久化卷**：
  - `docker-compose.yml` 挂载 `./data:/app/data`，重启容器后账号数据、系统配置与历史日志完好无损。
- [x] **GitHub Actions 工作流**：
  - 每日定时 cron 与手动 `workflow_dispatch` 均可触发镜像自动构建并推送至 GHCR。


```
