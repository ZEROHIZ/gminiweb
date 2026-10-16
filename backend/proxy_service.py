"""
backend/proxy_service.py

核心职责：
对外标准 API 网关与请求代理服务。
实现 OpenAI 规范兼容的 `/v1/chat/completions` 与 `/v1/images/generations` 接口分发，
集成多账号池最少活跃连接调度、单账号可配置并发信号量隔离、故障自动转移（Failover）以及高保真审计日志入库。

主要功能：
1. handle_chat_completion: 兼容 OpenAI 格式对话完成接口。
2. handle_image_generation: 兼容 OpenAI 格式图像生成接口。
3. handle_test_generate: 控制台测试页面的即时调试处理。
"""

import time
import uuid
from typing import Any
from fastapi import HTTPException
from pydantic import BaseModel, Field

from backend.account_manager import account_manager
from backend.log_service import log_service
from backend.settings_manager import settings_manager
from backend.media_service import media_service


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "gemini-2.5-flash"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None


class ImageGenerationRequest(BaseModel):
    prompt: str
    model: str = "imagen-3.0-generate-002"
    n: int = 1
    size: str = "1024x1024"
    response_format: str = "url"


class TestGenerateRequest(BaseModel):
    account_id: str | None = None
    operation: str = Field(default="chat_completion", description="chat_completion 或 generate_image")
    model: str | None = None
    prompt: str


class ProxyService:
    @staticmethod
    def _extract_prompt_from_messages(messages: list[ChatMessage]) -> str:
        """从 OpenAI 格式 messages 提取最终输入提示词"""
        if not messages:
            return ""
        if len(messages) == 1:
            return messages[0].content
        # 多轮对话拼接
        parts = []
        for m in messages:
            parts.append(f"{m.role.capitalize()}: {m.content}")
        return "\n".join(parts)

    async def handle_chat_completion(
        self,
        req: ChatCompletionRequest,
        preferred_account_id: str | None = None,
    ) -> dict[str, Any]:
        """处理 OpenAI 兼容的聊天补全请求"""
        settings = settings_manager.get_settings()
        start_time = time.time()
        prompt = self._extract_prompt_from_messages(req.messages)
        req_dict = req.model_dump()

        account = None
        last_error = None
        max_retries = 2 if settings.failover_retry else 1

        for attempt in range(max_retries):
            try:
                acc, client, sem = await account_manager.acquire_account(
                    preferred_id=preferred_account_id if attempt == 0 else None
                )
                account = acc

                account_manager.increment_active(acc.id)
                try:
                    async with sem:
                        target_model = None if req.model in ("default", "auto", "gemini-default", "") else req.model
                        output = await client.generate_content(
                            prompt=prompt,
                            model=target_model,
                        )
                        # 检查是否有自动更新的 Cookie 并回写
                        account_manager.persist_cookies_if_changed(acc.id, client)
                finally:
                    account_manager.decrement_active(acc.id)

                duration = time.time() - start_time
                reply_text = getattr(output, "text", "") or ""
                thoughts = getattr(output, "thoughts", "") or ""

                response_data = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": req.model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": reply_text,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": len(prompt),
                        "completion_tokens": len(reply_text),
                        "total_tokens": len(prompt) + len(reply_text),
                    },
                }
                images = getattr(output, "images", None) or []
                img_payload = []
                for img in images:
                    local_url = await media_service.save_image(img)
                    alt = getattr(img, "title", "image") or getattr(img, "alt", "image") or "image"
                    if local_url:
                        img_payload.append({"url": local_url, "alt": alt})

                if img_payload:
                    response_data["choices"][0]["message"]["images"] = img_payload

                if thoughts:
                    response_data["choices"][0]["message"]["reasoning_content"] = thoughts

                # 记录成功审计日志
                await log_service.add_log(
                    operation="chat_completion",
                    token_email=acc.email,
                    status="已完成",
                    progress="100%",
                    status_code=200,
                    summary="文本回答生成成功",
                    duration=duration,
                    request_data=req_dict,
                    response_data=response_data,
                )

                return response_data

            except Exception as e:
                last_error = e
                # 若还有重试机会，继续尝试下一个账号
                if attempt < max_retries - 1:
                    continue

        duration = time.time() - start_time
        err_msg = str(last_error) or "未知服务异常"
        err_response = {
            "error": {
                "message": err_msg,
                "type": "server_error",
                "code": 500,
            }
        }

        await log_service.add_log(
            operation="chat_completion",
            token_email=account.email if account else "未知",
            status="失败",
            progress="0%",
            status_code=500,
            summary=f"对话生成失败: {err_msg[:60]}",
            duration=duration,
            request_data=req_dict,
            error_reason=err_msg,
            error_response=err_response,
        )

        raise HTTPException(status_code=500, detail=err_response)

    async def handle_image_generation(
        self,
        req: ImageGenerationRequest,
        preferred_account_id: str | None = None,
    ) -> dict[str, Any]:
        """处理 OpenAI 兼容的图片生成请求"""
        settings = settings_manager.get_settings()
        start_time = time.time()
        req_dict = req.model_dump()

        account = None
        last_error = None
        max_retries = 2 if settings.failover_retry else 1

        for attempt in range(max_retries):
            try:
                acc, client, sem = await account_manager.acquire_account(
                    preferred_id=preferred_account_id if attempt == 0 else None
                )
                account = acc

                account_manager.increment_active(acc.id)
                try:
                    async with sem:
                        # 提示词强化与模型映射
                        gen_prompt = req.prompt
                        target_model = None
                        if req.model and req.model not in ("imagen-3.0-generate-002", "dall-e-3", "default", "auto", "gemini-default"):
                            target_model = req.model
                        output = await client.generate_content(
                            prompt=gen_prompt,
                            model=target_model,
                        )
                        account_manager.persist_cookies_if_changed(acc.id, client)
                finally:
                    account_manager.decrement_active(acc.id)

                duration = time.time() - start_time
                images = getattr(output, "images", []) or []

                if not images:
                    raise ValueError("Gemini 未返回任何生成的图片内容")

                image_items = []
                primary_url = ""
                for img in images:
                    local_url = await media_service.save_image(img)
                    if local_url:
                        if not primary_url:
                            primary_url = local_url
                        image_items.append({"url": local_url})

                response_data = {
                    "created": int(time.time()),
                    "data": image_items,
                }

                await log_service.add_log(
                    operation="generate_image",
                    token_email=acc.email,
                    status="已完成",
                    progress="100%",
                    status_code=200,
                    summary="图片结果已返回",
                    duration=duration,
                    request_data=req_dict,
                    response_data=response_data,
                    result_url=primary_url,
                )

                return response_data

            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    continue

        duration = time.time() - start_time
        err_msg = str(last_error) or "生图服务异常"
        status_code = 503 if "没有可用的 Token" in err_msg else 500
        err_response = {
            "error": err_msg,
            "status": "processing",
            "token_select_ms": int(duration * 1000),
        }

        await log_service.add_log(
            operation="generate_image",
            token_email=account.email if account else "未知",
            status="失败",
            progress="0%",
            status_code=status_code,
            summary=f"生成失败: {err_msg[:60]}",
            duration=duration,
            request_data=req_dict,
            error_reason=err_msg,
            error_response=err_response,
        )

        raise HTTPException(status_code=status_code, detail=err_response)

    async def handle_test_generate(self, req: TestGenerateRequest) -> dict[str, Any]:
        """前端测试面板专用的触发接口"""
        if req.operation == "generate_image":
            img_req = ImageGenerationRequest(
                prompt=req.prompt,
                model=req.model or "imagen-3.0-generate-002",
            )
            return await self.handle_image_generation(img_req, preferred_account_id=req.account_id)
        else:
            chat_req = ChatCompletionRequest(
                model=req.model or "gemini-2.5-flash",
                messages=[ChatMessage(role="user", content=req.prompt)],
            )
            return await self.handle_chat_completion(chat_req, preferred_account_id=req.account_id)


# 全局单例
proxy_service = ProxyService()
