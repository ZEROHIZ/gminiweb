# Gemini WebAPI Python 接口详细文档 (API Reference)

> 本文档通过逆向与源码分析整理，覆盖 `gemini-webapi` 客户端的核心类、参数规格、模型选择机制以及常用功能的使用方法。

---

## 1. 核心概述

`gemini-webapi` 是一个基于 Google Gemini Web 客户端逆向协议的异步 Python SDK。它支持：
- **模型自动发现与自适应切换**（无需硬编码，登录后动态拉取账号可用模型）。
- **多轮会话管理**（支持获取历史记录、继续会话、删除会话）。
- **多模态能力**（支持上传图片/音频/文本文件进行提问，接收并保存生成的图片与视频）。
- **高级特性**（Deep Research 深度研究模式、Extended Thinking 思考模式、Gems 定制角色）。

---

## 2. 模型选择机制（Model Selection）

### 2.1 是否支持选择模型？
**完全支持。** 
本库在调用 `client.init()` 时，会自动向 Google 服务器请求当前账户的状态与可用模型列表（`AvailableModel`）。

### 2.2 如何指定模型？
在 `client.generate_content(...)`、`client.generate_content_stream(...)` 或 `chat.send_message(...)` 中，均可以通过 `model` 参数指定模型。

`model` 参数支持以下传入方式：

| 传入方式 | 示例 | 说明 |
| :--- | :--- | :--- |
| **标准模型名 (str)** | `"gemini-2.5-flash"`, `"gemini-2.5-pro"` | 推荐方式，精确匹配 |
| **别名 / 简写 (str)** | `"flash"`, `"pro"`, `"flash-lite"` | 内置别名与模糊匹配，自动映射到对应版本 |
| **模型 ID (str)** | `"fbb127bbb056c959"` | 直接传入内部十六进制 ID |
| **AvailableModel 对象** | `client.resolve_model("pro")` 或 `client.list_models()[0]` | 动态解析出的对象实体 |
| **None (默认)** | 不传或传 `None` | 使用 Google 账号的默认模型 |
| **Model 枚举 (已弃用)** | `Model.BASIC_PRO` | *Deprecated*，为了兼容保留，运行时会提示 Warning |

### 2.3 查看当前账户支持的所有模型
```python
# 获取当前账号所有可用模型列表
models = client.list_models()
if models:
    for m in models:
        print(f"Name: {m.model_name:<20} Display: {m.display_name:<20} ID: {m.model_id}")
        print(f"  Description: {m.description}")
        print(f"  Aliases: {m.aliases}")
```

---

## 3. 核心客户端：`GeminiClient`

### 3.1 实例化与初始化

```python
from gemini_webapi import GeminiClient

client = GeminiClient(
    secure_1psid="...",
    secure_1psidts="...",
    proxy="http://127.0.0.1:7890",       # 可选，网络代理
    auto_refresh=True,                    # 可选，是否后台自动刷新 Cookie
    auto_close=False,                     # 可选，空闲是否自动关闭连接
    timeout=60.0,                         # 可选，请求超时时间（秒）
    verbose=False,                        # 可选，是否输出详细日志
)

# 必须调用 init() 进行握手初始化和模型列表拉取
await client.init(timeout=30.0, auto_refresh=True)
```

**参数详解：**
- `secure_1psid` (`str`): Google 登录凭据 Cookie `__Secure-1PSID`（必需）。
- `secure_1psidts` (`str`, 可选): Cookie `__Secure-1PSIDTS`（强烈建议配置，避免失效）。
- `proxy` (`str`, 可选): 代理地址，格式如 `http://127.0.0.1:7890` 或 `socks5://...`。
- `auto_refresh` (`bool`, 默认 `True`): 是否在后台启动 Task 自动轮换刷新凭据。
- `auto_close` (`bool`, 默认 `False`): 设定在一段时间无请求后是否自动关闭 session。

---

### 3.2 文本/多模态生成：`generate_content`

```python
response = await client.generate_content(
    prompt="用 Python 实现快速排序",
    files=["./image.png"],          # 可选，文件路径或 BytesIO
    model="gemini-2.5-pro",         # 可选，指定模型
    temporary=False,                # 可选，为 True 时不存入网页端历史记录
    deep_research=False,            # 可选，开启深度研究
    extended_thinking=False,        # 可选，开启深度思考模式
)

print(response.text)
```

**方法签名：**
```python
async def generate_content(
    self,
    prompt: str,
    files: list[str | Path | bytes | io.BytesIO] | None = None,
    model: AvailableModel | Model | str | dict | None = None,
    gem: Gem | str | None = None,
    chat: Optional["ChatSession"] = None,
    temporary: bool = False,
    deep_research: bool = False,
    extended_thinking: bool = False,
    **kwargs,
) -> ModelOutput
```

**主要参数：**
- `prompt` (`str`): 用户提示词（不能为空）。
- `files` (`list`, 可选): 支持本地文件路径字符串、`Path` 对象、或 `bytes` 字节流。会自动上传到 Google 存储。
- `model` (`str | AvailableModel`, 可选): 生成所使用的模型。
- `gem` (`Gem | str`, 可选): 指定使用的 Gem 智能体角色或 ID。
- `chat` (`ChatSession`, 可选): 关联的会话对象；不传则为单次独立问答。
- `temporary` (`bool`, 默认 `False`): 临时无痕对话，不保留在网页端左侧历史。
- `extended_thinking` (`bool`, 默认 `False`): 开启扩展思考模式（Thinking Process）。

---

### 3.3 流式生成：`generate_content_stream`

```python
async for chunk in client.generate_content_stream(
    prompt="写一篇关于量子计算的短文",
    model="flash",
):
    # chunk 为 ModelOutput 对象
    # text_delta 为当前增量文本，text 为目前累积的完整文本
    print(chunk.text_delta, end="", flush=True)
    
    # 如果模型在思考，可获取思考过程
    if chunk.thoughts_delta:
        print(f"[思考中]: {chunk.thoughts_delta}", end="")
```

---

### 3.4 多轮会话：`start_chat` 与 `ChatSession`

用于在代码中维护连续对话上下文：

```python
# 开启一个会话
chat = client.start_chat(
    model="gemini-2.5-flash", 
    temporary=False
)

# 第 1 轮
res1 = await chat.send_message("你好，我是张三。")
print(res1.text)

# 第 2 轮（自动继承上下文）
res2 = await chat.send_message("我刚才说了我叫什么？")
print(res2.text)

# 流式发送
async for chunk in chat.send_message_stream("请用古文写一首赞美春天的诗"):
    print(chunk.text_delta, end="", flush=True)

# 获取当前会话历史
history = await chat.read_history(limit=10)
```

---

### 3.5 历史对话读取与删除

```python
# 读取指定 cid (Conversation ID) 的历史记录
history = await client.read_chat(cid="c_...", limit=20)

# 删除指定对话
await client.delete_chat(cid="c_...")
```

---

## 4. 响应结构解析：`ModelOutput`

调用 `generate_content` 返回的核心结果对象为 `ModelOutput`，包含以下关键字段与属性：

| 属性 / 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `text` | `str` | 模型的主回复文本内容 |
| `text_delta` | `str` | 流式响应时的单次增量文本 |
| `thoughts` | `str \| None` | 模型的完整思考链路（Thinking/Reasoning 文本） |
| `thoughts_delta` | `str` | 流式时的思考增量 |
| `images` | `list[Image]` | 模型生成的图片列表（Imagen 3） |
| `videos` | `list[GeneratedVideo]` | 模型生成的视频列表（Veo） |
| `citations` | `list[Citation]` | 回复中引用的网页来源与链接 |
| `metadata` | `list[str]` | 对话元数据，通常包含 `[cid, rid, rcid]` |
| `candidates` | `list[Candidate]` | 模型返回的所有备选回复列表（默认选中第 0 个） |

### 4.1 图片与视频的保存示例

```python
response = await client.generate_content("生成一只可爱的小柴犬图片")

# 保存图片
if response.images:
    for i, img in enumerate(response.images):
        # image.save 会自动从 Google CDN 下载原始高分辨率图片
        file_path = await img.save(path="./output", filename=f"dog_{i}.png")
        print(f"图片保存至: {file_path}")

# 保存视频
if response.videos:
    for i, vid in enumerate(response.videos):
        file_path = await vid.save(path="./output", filename=f"video_{i}.mp4")
        print(f"视频保存至: {file_path}")
```

---

## 5. 异常体系 (`gemini_webapi.exceptions`)

所有库内异常均继承自 `GeminiError`：

```python
from gemini_webapi.exceptions import (
    GeminiError,           # 顶级基类
    APIError,              # HTTP 状态码异常或 Google RPC 返回错误
    TimeoutError,          # 请求超时
    ModelInvalidError,     # 传入的模型名称不存在或未授权
    AccountStatusError,    # 账户状态受限（如地域受限、未成年账号、风控阻断）
)
```

典型异常处理范式：
```python
try:
    response = await client.generate_content("hello", model="unknown-model")
except ModelInvalidError as e:
    print(f"模型名称无效: {e}")
except APIError as e:
    print(f"API 请求失败: {e.status_code} - {e.message}")
except GeminiError as e:
    print(f"Gemini 基础异常: {e}")
```
