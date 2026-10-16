"""
backend/chat_service.py

核心职责：
Gemini 网页端历史会话与消息详情提取服务。封装底层 GeminiClient 对历史对话列表的检索
与对话轮次（turns）解析，输出包含角色、文本、思考链路（extended thoughts）与图片资产的结构化数据。

主要功能：
1. list_account_chats: 获取指定账号在网页端的历史会话摘要列表。
2. get_chat_turns: 读取指定会话 ID 的完整对话轮次并结构化返回。
"""

from typing import Any
from backend.account_manager import account_manager


class ChatService:
    async def list_account_chats(self, account_id: str) -> list[dict[str, Any]]:
        """获取账号在 Gemini 网页端的历史会话列表"""
        acc = account_manager.get_account(account_id)
        if not acc:
            raise ValueError(f"未找到账号: {account_id}")

        client = await account_manager.get_or_create_client(account_id)
        chats = client.list_chats()
        if not chats:
            return []

        results = []
        for c in chats:
            results.append({
                "cid": c.cid,
                "title": c.title or "无标题会话",
                "is_pinned": getattr(c, "is_pinned", False),
                "timestamp": str(getattr(c, "timestamp", "") or ""),
            })
        return results

    async def get_chat_turns(self, account_id: str, cid: str) -> dict[str, Any]:
        """获取指定会话的历史对话详情"""
        acc = account_manager.get_account(account_id)
        if not acc:
            raise ValueError(f"未找到账号: {account_id}")

        client = await account_manager.get_or_create_client(account_id)
        history = client.read_chat(cid=cid)
        if not history or not getattr(history, "turns", None):
            return {"cid": cid, "turns": []}

        turns_data = []
        for turn in history.turns:
            role = turn.role
            text = turn.text or ""
            thoughts = ""
            images = []

            output = getattr(turn, "model_output", None)
            if output:
                if getattr(output, "thoughts", None):
                    thoughts = output.thoughts
                imgs = getattr(output, "images", None) or []
                for img in imgs:
                    images.append({
                        "url": getattr(img, "url", ""),
                        "title": getattr(img, "title", ""),
                        "alt": getattr(img, "alt", ""),
                    })

            turns_data.append({
                "role": role,
                "text": text,
                "thoughts": thoughts,
                "images": images,
            })

        return {"cid": cid, "turns": turns_data}


# 全局单例
chat_service = ChatService()
