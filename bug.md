# 项目 Bug 修复记录归档 (bug.md)

本文档根据开发准则（Rule 4: 目标驱动执行与 Bug 档案记录）建立，记录每次修复的 Bug 根因、复现场景及最终解决方案，作为历史参考并防止未来引入回归。

---

### [BUG-001] SQLite WAL 模式下执行 VACUUM 报错 "cannot VACUUM from within a transaction"
- **发生时间**：2026-10-15
- **影响模块**：`backend/log_service.py` (`clear_logs`)
- **现象描述**：执行一键清空日志接口或单元测试时，抛出 `sqlite3.OperationalError: cannot VACUUM from within a transaction`。
- **根本原因 (Root Cause)**：
  SQLite 引擎规定 `VACUUM` 指令不能在活动事务（Transaction）内部执行。`aiosqlite.connect()` 默认开启了事务管理（隐式 `BEGIN`），在 `DELETE FROM logs` 后紧接着执行 `VACUUM` 导致违背引擎规则。
- **解决方案 (Solution)**：
  在 `clear_logs()` 建立数据库连接时明确指定 `isolation_level=None`（自动提交模式），直接依序执行 `DELETE FROM logs` 与 `VACUUM`，彻底解决事务嵌套问题。
- **防退化验证**：`tests/test_log_service.py::test_log_service_lifecycle` 执行通过。

---

### [BUG-002] 未经生命周期启动时查询日志抛出 "no such table: logs"
- **发生时间**：2026-10-15
- **影响模块**：`backend/log_service.py` (`get_logs`, `add_log`, `get_log_by_id`, `cleanup_old_logs`)
- **现象描述**：当测试用例、脚本或未经 FastAPI lifespan 预热的外部上下文直接调用 `log_service` 查询日志时，因数据库表尚未初始化而抛出 `OperationalError: no such table: logs`。
- **根本原因 (Root Cause)**：
  数据库与表的初始化只放在了 `main.py` 的 FastAPI `lifespan` 钩子中，`LogService` 自身缺乏自愈（Self-healing）机制，无法独立应对未初始化的直接调用。
- **解决方案 (Solution)**：
  在 `LogService` 内部增加 `_initialized` 状态与异步 `_ensure_db()` 方法。在执行任何增删改查前自检，如未初始化则自动幂等执行 WAL 配置、建表与建索引，实现服务层面的闭环自愈。
- **防退化验证**：`tests/test_api_endpoints.py::test_logs_api` 无需依赖完整生命周期即可稳定执行通过。

---

### [BUG-003] 编辑账号弹窗打开时原有配置与凭据显示为空白
- **发生时间**：2026-10-16
- **影响模块**：`backend/main.py` (`get_account_detail`), `frontend/app.js` (`editAccount`, `openAccountModal`)
- **现象描述**：用户在面板列表中点击“编辑”某个已配置的 Gemini 账号时，弹窗中的备注及 Cookie/Key 1/Key 2 均呈现完全空白，造成“数据未保存”或无法微调凭据的困扰。
- **根本原因 (Root Cause)**：
  1. `/api/accounts` 列表接口对凭据执行了只读打码脱敏（`secure_1psid_masked`），缺乏供管理员面板编辑使用的单账号完整凭据回显接口；
  2. 前端 `openAccountModal` 在编辑态下直接将输入框赋空（`value = ''`），寄希望于用户留空沿用，破坏了所见即所得的编辑体验。
- **解决方案 (Solution)**：
  1. 在 `backend/main.py` 增加 `GET /api/accounts/{account_id}` 接口（强依赖管理员登录权限），向面板回传完整的 `secure_1psid` 和 `secure_1psidts`；
  2. 前端 `editAccount` 改为异步获取真实数据后再展开 Modal，并将 Key 1、Key 2、备注名、代理完整回填到各个输入框，支持直接人工微调；
  3. 彻底移除了“账号标识 / 邮箱 (选填)”这一冗余表单行，直接以“账号备注名称”作为唯一标识。
- **防退化验证**：`tests/test_api_endpoints.py` 补充了详情回显测试用例，17 项用例全部 100% 通过。
### [BUG-004] 测试页面与代理接口指定 gemini-pro 或特定生图模型名时导致生图失败
- **发生时间**：2026-10-16
- **影响模块**：`backend/proxy_service.py` (`handle_chat_completion`, `handle_image_generation`), `frontend/app.js` (`updateTestModelOptions`)
- **现象描述**：用户在测试页面输入“生成一张猫咪的图片”并选择 `gemini-pro` 时，模型回复“请恕我爱莫能助，我只是一个语言模型，不能理解或回复你的这个问题”；且外部请求传入 `imagen-3.0-generate-002` 时会导致客户端找不到模型名异常。
- **根本原因 (Root Cause)**：
  1. Google Gemini 官方 Web 端架构中，`gemini-pro`（Gemini 2.5 Pro）是高阶纯文本推理模型，Google 官方在 Web 端没有给 Pro 分配 Imagen 生图工具（Tool）；生图仅在默认模型（Default Flash 路由）下才能触发 Google 底层的 Imagen 3 扩散生图；
  2. 逆向协议 `gemini-webapi` 中没有独立的 `imagen-3.0-generate-002` 模型名，直接传入特定模型头会阻断默认生图工具路由。
- **解决方案 (Solution)**：
  1. 在 `frontend/app.js` 模型下拉框首位加入“🌟 默认模型 (推荐：支持画图生图与全功能对话)”选项（值为 `default`），并给 `gemini-pro` 标注“纯文本/复杂推理，不支持生图”；
  2. 在 `backend/proxy_service.py` 中将 `default` / `auto` 及 `imagen-3.0-generate-002` 自动归一化映射为 `target_model = None`（让 Google Web 走默认多模态生图管道）。
- **防退化验证**：
  1. 后端实测 `POST /api/test/generate` 传入 `model: "default"` 与 `model: null`，均 100% 成功返回 200 OK，不再报错 `Unknown model name: 'default'`；
### [BUG-005] 生成图片返回 Google 原始 CDN 外链导致其他浏览器与前端遭遇 HTTP 403 碎图
- **发生时间**：2026-10-16
- **影响模块**：`backend/proxy_service.py`, `backend/media_service.py`, `frontend/app.js`, `frontend/index.html`
- **现象描述**：Gemini 生成图片后返回形如 `https://lh3.googleusercontent.com/gg-dl/...` 的 Google CDN 原始地址。由于 Google 实施强鉴权绑定，其他未携带对应 Cookie 的浏览器直接打开或前端代理匿名抓取均报 `HTTP 403 Forbidden`，导致图片破损显示为 `[Generated Image 0]`。
- **根本原因 (Root Cause)**：
  Google 生成图 CDN 需要特定的会话凭证（Cookie/Session）才能拉取下载，直接将外链暴露给前端或客户端破坏了可用性与跨浏览器访问能力。
- **解决方案 (Solution)**：
  1. 新增 `backend/media_service.py` 资产服务，利用 SDK 内部会话（`img.save()`）在生成时将图片自动持久化落地至 `data/images/`；
  2. 在 `backend/main.py` 挂载本地静态文件路由 `/media/images`，将对外的图片 URL 全面重写为本服务的安全本地路径；
  3. 在前端“请求日志”与“测试页面”之间新增【图片管理】标签页，提供已落盘图片总数/磁盘占用统计、卡片画廊、在新窗口查看原图、单张删除及一键清空功能；
  4. 测试页面（Chat Studio）前端适配直接渲染 `/media/images` 本地链接，彻底杜绝跨域防盗链碎图。
- **防退化验证**：
  1. `tests/test_api_endpoints.py::test_image_management_endpoints` 覆盖列表、静态访问、单删与 404 容错；
  2. 18 项自动化测试 100% 全部通过；
  3. 实测 `GET /media/images/...` 毫秒级返回 `image/jpeg`（2.65 MB），零 403。

