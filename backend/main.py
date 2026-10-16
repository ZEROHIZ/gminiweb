"""
backend/main.py

核心职责：
FastAPI 主服务入口与路由中枢。
负责应用生命周期管理（SQLite WAL 初始化、后台健康巡检器启动/优雅停止）、
挂载各功能路由（安全认证、账号管理、历史会话、系统配置、请求日志、对外网关及测试调试）、
实现 Google CDN 媒体流式反代 (/api/proxy/image) 以及静态托管现代化 SPA 前端界面。

主要功能：
1. lifespan: 容器生命周期非阻塞冷启动与平滑退出管理。
2. 认证路由 (/api/auth/*): 面板登录、登出、状态感知。
3. 账号路由 (/api/accounts/*): 账号增删改查、单账号即时检测。
4. 会话路由 (/api/accounts/{id}/chats/*): 网页历史会话提取与对话详情浏览。
5. 配置路由 (/api/settings/*): 动态配置读取与保存、立即巡检触发。
6. 日志路由 (/api/logs/*): 审计日志分页筛选、单条详情拉取、一键清空。
7. 测试路由 (/api/test/*): 控制台即时请求测试。
8. 媒体反代 (/api/proxy/image): 解决国内网络 Google 图片预览破损问题。
9. 网关路由 (/v1/*): 对外标准 OpenAI 兼容接口代理池。
10. 前端托管: 自动托管 frontend/ 单页应用。
"""

import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.account_manager import account_manager
from backend.auth import create_panel_token, verify_api_key, verify_panel_auth
from backend.chat_service import chat_service
from backend.health_checker import health_checker
from backend.log_service import log_service
from backend.proxy_service import (
    ChatCompletionRequest,
    ImageGenerationRequest,
    TestGenerateRequest,
    proxy_service,
)
from backend.settings_manager import settings_manager
from backend.media_service import media_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 优先就绪数据库 WAL 与必要目录
    await log_service.init_db()
    # 2. 启动常驻后台健康巡检任务（非阻塞预热账号）
    health_checker.start()
    yield
    # 3. 优雅停止后台巡检器
    health_checker.stop()


app = FastAPI(
    title="Gemini Account Manager & Gateway API",
    version="1.0.0",
    description="生产级多账号管理面板、Cookie持久化回写与兼容 OpenAI 代理网关",
    lifespan=lifespan,
)

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 1. 认证路由 ====================

class LoginRequest(BaseModel):
    password: str


@app.get("/api/auth/status")
async def get_auth_status():
    """获取面板鉴权状态"""
    settings = settings_manager.get_settings()
    return {
        "auth_required": bool(settings.panel_password),
    }


@app.post("/api/auth/login")
async def login(req: LoginRequest, response: Response):
    """管理员登录校验"""
    settings = settings_manager.get_settings()
    if not settings.panel_password:
        token = create_panel_token()
        response.set_cookie("panel_token", token, httponly=True, max_age=86400 * 7, samesite="lax")
        return {"success": True, "token": token, "message": "免密登录成功"}

    if hmac.compare_digest(req.password.strip(), settings.panel_password.strip()):
        token = create_panel_token()
        response.set_cookie("panel_token", token, httponly=True, max_age=86400 * 7, samesite="lax")
        return {"success": True, "token": token, "message": "登录成功"}

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="面板访问密码错误")


@app.post("/api/auth/logout")
async def logout(response: Response):
    """退出登录"""
    response.delete_cookie("panel_token")
    return {"success": True, "message": "已退出登录"}


# ==================== 2. 账号管理路由 ====================

class AddAccountRequest(BaseModel):
    email: str = ""  # 选填：留空则自动提取或使用备注作为标识
    raw_cookie_or_psid: Any
    psidts: str = ""
    remark: str = ""
    proxy: str | None = None
    is_enabled: bool = True
    concurrency_limit: int | None = None


class UpdateAccountRequest(BaseModel):
    email: str | None = None
    raw_cookie_or_psid: Any = None
    psidts: str | None = None
    remark: str | None = None
    proxy: str | None = None
    is_enabled: bool | None = None
    status: str | None = None
    concurrency_limit: int | None = None


@app.get("/api/accounts", dependencies=[Depends(verify_panel_auth)])
async def get_accounts():
    """获取脱敏账号列表与状态配额"""
    return account_manager.get_public_accounts()


@app.post("/api/accounts", dependencies=[Depends(verify_panel_auth)])
async def add_account(req: AddAccountRequest):
    """添加或批量导入 Cookie 创建账号"""
    try:
        acc = account_manager.add_account(
            email=req.email,
            raw_cookie_or_psid=req.raw_cookie_or_psid,
            psidts=req.psidts,
            remark=req.remark,
            proxy=req.proxy,
            is_enabled=req.is_enabled,
            concurrency_limit=req.concurrency_limit,
        )
        return {"success": True, "account": acc}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/accounts/{account_id}", dependencies=[Depends(verify_panel_auth)])
async def update_account(account_id: str, req: UpdateAccountRequest):
    """更新账号信息与凭据"""
    try:
        acc = account_manager.update_account(
            account_id=account_id,
            email=req.email,
            raw_cookie_or_psid=req.raw_cookie_or_psid,
            psidts=req.psidts,
            remark=req.remark,
            proxy=req.proxy,
            is_enabled=req.is_enabled,
            status=req.status,
            concurrency_limit=req.concurrency_limit,
        )
        return {"success": True, "account": acc}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/accounts/{account_id}", dependencies=[Depends(verify_panel_auth)])
async def get_account_detail(account_id: str):
    """获取单个账号的完整配置与凭据（用于编辑弹窗数据回显）"""
    acc = account_manager.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return {
        "id": acc.id,
        "email": acc.email,
        "remark": acc.remark,
        "secure_1psid": acc.secure_1psid,
        "secure_1psidts": acc.secure_1psidts,
        "proxy": acc.proxy or "",
        "is_enabled": acc.is_enabled,
        "status": acc.status,
        "models": acc.models or [],
        "concurrency_limit": acc.concurrency_limit,
    }


@app.delete("/api/accounts/{account_id}", dependencies=[Depends(verify_panel_auth)])
async def delete_account(account_id: str):
    """删除指定账号"""
    ok = account_manager.delete_account(account_id)
    if not ok:
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"success": True, "message": "账号已删除"}


@app.post("/api/accounts/{account_id}/check", dependencies=[Depends(verify_panel_auth)])
async def check_single_account(account_id: str):
    """手动立即检测单个账号健康状态"""
    result = await account_manager.check_health(account_id, source="manual")
    return {"success": True, "result": result}


@app.get("/api/accounts/{account_id}/health-logs", dependencies=[Depends(verify_panel_auth)])
async def get_account_health_logs(
    account_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """获取指定账号的有效性巡检与保活轮换日志"""
    acc = account_manager.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")

    identifiers = [acc.remark, acc.email, acc.id]
    logs_data = await log_service.get_account_health_logs(
        token_identifiers=identifiers,
        page=page,
        page_size=page_size,
    )
    return {
        "success": True,
        "account_id": acc.id,
        "account_remark": acc.remark or acc.email,
        "account_status": acc.status,
        **logs_data,
    }


# ==================== 3. 网页会话路由 ====================

@app.get("/api/accounts/{account_id}/chats", dependencies=[Depends(verify_panel_auth)])
async def get_account_chats(account_id: str):
    """读取指定账号网页端历史会话列表"""
    try:
        chats = await chat_service.list_account_chats(account_id)
        return {"success": True, "chats": chats}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/accounts/{account_id}/chats/{cid}", dependencies=[Depends(verify_panel_auth)])
async def get_chat_history(account_id: str, cid: str):
    """读取会话完整对话详情"""
    try:
        history = await chat_service.get_chat_turns(account_id, cid)
        return {"success": True, "history": history}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==================== 4. 系统配置路由 ====================

@app.get("/api/settings", dependencies=[Depends(verify_panel_auth)])
async def get_settings():
    """获取当前系统配置"""
    s = settings_manager.get_settings()
    data = s.model_dump()
    data["has_panel_password"] = bool(data.get("panel_password"))
    if data.get("panel_password"):
        data["panel_password"] = "******"
    return data


@app.post("/api/settings", dependencies=[Depends(verify_panel_auth)])
async def update_settings(data: dict):
    """更新系统配置（动态重置巡检定时器）"""
    if data.get("panel_password") == "******":
        del data["panel_password"]
    old_interval = settings_manager.get_settings().health_check_interval_minutes
    updated = settings_manager.update_settings(data)
    if updated.health_check_interval_minutes != old_interval:
        health_checker.notify_interval_changed()
    return {"success": True, "settings": updated}


@app.post("/api/settings/check-now", dependencies=[Depends(verify_panel_auth)])
async def trigger_check_now():
    """立即触发一次全量账号健康巡检"""
    health_checker.trigger_check_now()
    return {"success": True, "message": "已成功触发全量账号健康巡检"}


# ==================== 5. 日志审计路由 ====================

@app.get("/api/logs", dependencies=[Depends(verify_panel_auth)])
async def get_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    status: str | None = Query(default=None),
    operation: str | None = Query(default=None),
):
    """分页拉取请求日志列表"""
    return await log_service.get_logs(
        page=page,
        page_size=page_size,
        status=status,
        operation=operation,
    )


@app.get("/api/logs/{log_id}", dependencies=[Depends(verify_panel_auth)])
async def get_log_detail(log_id: int):
    """获取单条日志详情用于弹窗展示"""
    detail = await log_service.get_log_by_id(log_id)
    if not detail:
        raise HTTPException(status_code=404, detail="日志记录不存在")
    return detail


@app.delete("/api/logs", dependencies=[Depends(verify_panel_auth)])
async def clear_logs():
    """一键清空所有日志"""
    await log_service.clear_logs()
    return {"success": True, "message": "所有请求日志已成功清空"}


# ==================== 6. 控制台在线测试路由 ====================

@app.post("/api/test/generate", dependencies=[Depends(verify_panel_auth)])
async def test_generate(req: TestGenerateRequest):
    """测试页面即时调用"""
    return await proxy_service.handle_test_generate(req)


# ==================== 7. 媒体反向代理中转 ====================

@app.get("/api/proxy/image")
async def proxy_image(url: str = Query(...)):
    """
    流式反向代理 Google CDN 图片，解决国内前端网络无法直接加载破损问题。
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="非法图片 URL")

    settings = settings_manager.get_settings()
    proxy = settings.global_proxy or None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Referer": "https://gemini.google.com/",
    }

    client = httpx.AsyncClient(proxy=proxy, follow_redirects=True, timeout=30.0)
    try:
        req = client.build_request("GET", url, headers=headers)
        r = await client.send(req, stream=True)
        if r.status_code != 200:
            await client.aclose()
            raise HTTPException(status_code=r.status_code, detail=f"拉取图片失败: HTTP {r.status_code}")

        async def _streamer():
            try:
                async for chunk in r.aiter_bytes(chunk_size=8192):
                    yield chunk
            finally:
                await r.aclose()
                await client.aclose()

        media_type = r.headers.get("content-type", "image/jpeg")
        return StreamingResponse(_streamer(), media_type=media_type)
    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"图片反代中转异常: {e}")


# ==================== 8. 本地图片资产管理路由 ====================

@app.get("/api/images", dependencies=[Depends(verify_panel_auth)])
async def list_images():
    """获取所有已保存的本地图片资产列表及统计"""
    return media_service.list_images()


@app.delete("/api/images/{filename}", dependencies=[Depends(verify_panel_auth)])
async def delete_image(filename: str):
    """删除指定的本地图片"""
    deleted = media_service.delete_image(filename)
    if not deleted:
        raise HTTPException(status_code=404, detail="图片不存在或已删除")
    return {"success": True, "message": f"已成功删除图片 {filename}"}


@app.post("/api/images/clear", dependencies=[Depends(verify_panel_auth)])
async def clear_all_images():
    """一键清空所有已保存的本地图片"""
    cleared = media_service.clear_all_images()
    return {"success": True, "count": cleared, "message": f"已成功清空 {cleared} 张本地图片"}


# ==================== 9. 对外标准网关路由 (OpenAI 格式) ====================

@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
async def chat_completions(req: ChatCompletionRequest):
    """对外兼容 OpenAI Chat Completion 接口"""
    return await proxy_service.handle_chat_completion(req)


@app.post("/v1/images/generations", dependencies=[Depends(verify_api_key)])
async def image_generations(req: ImageGenerationRequest):
    """对外兼容 OpenAI Image Generations 接口"""
    return await proxy_service.handle_image_generation(req)


# ==================== 10. 静态资源与前端单页托管 ====================

MEDIA_IMAGES_DIR = Path(__file__).resolve().parent.parent / "data" / "images"
MEDIA_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media/images", StaticFiles(directory=str(MEDIA_IMAGES_DIR)), name="media_images")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def index_root():
        index_file = FRONTEND_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return {"status": "ok", "message": "Gemini Account Manager API running"}

