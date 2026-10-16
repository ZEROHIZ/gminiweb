# Gemini 账号管理面板与 OpenAI 兼容代理网关系统

基于 [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API) 构建的生产级多账号管理控制台与对外 OpenAI 格式兼容调用网关。

支持**多账号状态监控、动态配额解析、Cookie 自动刷新回写持久化、SQLite WAL 详细审计日志、Google CDN 媒体反向代理中转、单容器轻量部署以及每日自动构建无感更新**。

---

## ✨ 核心特性

1. **多账号状态监控与动态配额解析 (Token 管理)**
   - 集中管理多个 Gemini 账号凭据，实时解析 Flash / Pro / Compute Quotas 配额及可用模型。
   - 单账号并发信号量隔离（默认并发度 2），杜绝并发超限触发 Google 429 封控。
   - 账号凭据自动脱敏展示，保障公网访问安全。

2. **多格式 Cookie 智能解析导入**
   - 支持浏览器插件（Cookie-Editor、EditThisCookie）导出的 JSON 数组格式直接粘贴。
   - 支持扁平键值对 `{"__Secure-1PSID": "...", "__Secure-1PSIDTS": "..."}` 及原始标头字符串。
   - 自动识别并提取凭据及过期时间，大幅降低账号录入难度。

3. **Cookie 动态轮换刷新与持久化原子回写（核心防雪崩）**
   - 当底层客户端刷新轮换 `__Secure-1PSIDTS` 时，后台主动捕获并将新凭据原子持久化回写至 `data/accounts.json`。
   - 结合 Watchtower 容器自动更新重启后，仍能读取最新有效凭据，杜绝“重启全军失效”。

4. **对外标准 API 网关代理 (OpenAI 协议兼容)**
   - 统一入口：`/v1/chat/completions` 与 `/v1/images/generations`。
   - 可无缝接入 NextChat、Cherry Studio、LobeChat、OneAPI、NewAPI 等第三方客户端。
   - 支持多账号轮询（Round-Robin）调度与故障自动转移（Failover）。

5. **详细请求审计日志 (SQLite WAL 模式)**
   - 完整记录调用操作（`generate_image` / `chat_completion`）、账号邮箱、状态、进度、状态码、耗时与结果摘要。
   - **高保真日志详情弹窗**：成功态支持直接预览图片与请求报文；失败态展示错误卡片与响应 JSON。
   - 采用 SQLite WAL 模式，高频读写无锁库，支持按条数与天数自动滚动淘汰。

6. **国内网络友好型媒体反向代理 (`/api/proxy/image`)**
   - 针对国内无法直连 Google CDN (`flow-content.google.com` / `lh3.googleusercontent.com`) 的问题，后端提供安全流式反向代理中转，彻底解决控制台与客户端图片破损。

7. **现代化 SPA 前端控制台**
   - 前后端一体化，由 FastAPI 静态托管，无需 Node.js 构建环境，极低资源占用（内存 < 100MB）。
   - 包含四大模块：【Token 管理】、【系统配置】、【请求日志】、【测试页面】。

8. **每日全自动无缝构建与更新**
   - GitHub Actions 每日凌晨自动构建（附带动态 `CACHE_BUST` 参数彻底打破 Docker 缓存，确保拉取上游最新代码）。
   - 配合服务器端已有 Watchtower 容器，实现完全免人工维护的持续更新。

---

## 🚀 快速开始

### 方式一：Docker 一行命令极速启动（推荐，直接复制）

Linux / macOS:
```bash
docker run -d --name gemini-manager --restart unless-stopped -p 8000:8000 -v $(pwd)/data:/app/data -e TZ=Asia/Shanghai -l "com.centurylinklabs.watchtower.enable=true" ghcr.io/zerohiz/gminiweb:latest
```

Windows (PowerShell):
```powershell
docker run -d --name gemini-manager --restart unless-stopped -p 8000:8000 -v ${PWD}/data:/app/data -e TZ=Asia/Shanghai -l "com.centurylinklabs.watchtower.enable=true" ghcr.io/zerohiz/gminiweb:latest
```

> **说明**：宿主机若已运行 Watchtower，会自动监控并无感更新该容器；启动后数据和日志自动持久化在本地 `./data` 目录下，在浏览器中打开 `http://<服务器IP或127.0.0.1>:8000` 即可直接使用。

---

### 方式二：Docker Compose 部署

在服务器创建 `docker-compose.yml`：

```yaml
services:
  gemini-manager:
    image: ghcr.io/zerohiz/gminiweb:latest
    container_name: gemini-manager
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
      - PANEL_PASSWORD=   # 可选：面板管理访问密码，留空则免密
      - API_KEY=          # 可选：对外网关访问密钥，留空则免密
      - GLOBAL_PROXY=     # 可选：全局网络代理，如 http://127.0.0.1:7890
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
```

启动容器：
```bash
docker compose up -d
```

---

### 方式三：本地 Python 环境直接运行

```bash
# 1. 创建虚拟环境并激活
python -m venv .venv
# Windows:
.\.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 2. 安装后端依赖
pip install -r backend/requirements.txt

# 3. 启动本地服务
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```
打开浏览器访问：`http://127.0.0.1:8000` 即可进入管理面板。

---

## ⚙️ 环境变量速查

| 环境变量名 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `PANEL_PASSWORD` | 控制台管理面板登录密码，留空则免密进入 | 留空 (免密) |
| `API_KEY` | 对外 OpenAI 兼容网关接口 (`/v1/*`) 的访问密钥 | 留空 (免密) |
| `GLOBAL_PROXY` | 默认全局 HTTP/SOCKS 代理地址 | 留空 (直连) |
| `DATA_DIR` | 账号数据、系统配置及 SQLite 日志持久化存储目录 | `/app/data` (容器内) 或 `data` |
| `PORT` | Web 服务监听端口 | `8000` |

---

## 📡 核心 API 路由速查

| 请求方法 | 路由地址 | 说明 | 鉴权要求 |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/auth/login` | 管理员密码登录 | 免鉴权 |
| `GET` | `/api/auth/status` | 获取面板是否开启密码防护 | 免鉴权 |
| `GET` | `/api/accounts` | 获取所有账号及动态配额（凭据已脱敏） | 面板权限 |
| `POST` | `/api/accounts` | 添加账号（支持 Cookie-Editor JSON 导入） | 面板权限 |
| `PUT` | `/api/accounts/{id}` | 更新账号信息或替换凭据 | 面板权限 |
| `DELETE`| `/api/accounts/{id}` | 删除账号 | 面板权限 |
| `POST` | `/api/accounts/{id}/check`| 立即检测指定账号健康状态 | 面板权限 |
| `GET` | `/api/accounts/{id}/chats`| 获取指定账号网页端历史会话列表 | 面板权限 |
| `GET` | `/api/accounts/{id}/chats/{cid}` | 获取特定会话完整对话轮次 | 面板权限 |
| `GET` | `/api/settings` | 读取系统配置 | 面板权限 |
| `POST` | `/api/settings` | 更新配置（动态生效重置定时器） | 面板权限 |
| `POST` | `/api/settings/check-now`| 立即触发全量账号健康巡检 | 面板权限 |
| `GET` | `/api/logs` | 分页筛选请求审计日志 | 面板权限 |
| `GET` | `/api/logs/{id}` | 获取单条日志详细报文（供弹窗查看） | 面板权限 |
| `DELETE`| `/api/logs` | 一键清空所有历史日志并回收空间 | 面板权限 |
| `POST` | `/api/test/generate` | 在测试页面触发请求调试 | 面板权限 |
| `GET` | `/api/proxy/image?url=...` | 流式反代 Google CDN 图片（解决破损） | 公开 |
| `POST` | `/v1/chat/completions` | **OpenAI 兼容对话补全（多账号池代理）** | `API_KEY` |
| `POST` | `/v1/images/generations` | **OpenAI 兼容生图接口** | `API_KEY` |

---

## 🧪 自动化测试

项目内置针对配置、鉴权、Cookie 提取、日志管理与 API 路由的单元及集成测试：

```bash
.\.venv\Scripts\python -m pytest tests
```

---

## 📜 许可证

遵循 MIT 开源许可证。
