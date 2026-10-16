"""
backend/media_service.py

核心职责：
负责本地持久化图片资产的统一管理。
包括从 Gemini 返回的 Image 对象自动落盘下载、本地媒体静态路径转换、
图片文件列表检索（大小、时间、元数据）、单张删除与一键清空功能，
确保用户能够随时查看与清理磁盘中的生成图片。

主要功能：
1. save_image: 将生成的 Image 对象安全保存至本地 data/images 目录并返回访问 URL。
2. list_images: 检索所有已保存的图片资产并计算占用磁盘大小。
3. delete_image: 安全删除指定图片（防止路径遍历攻击）。
4. clear_all_images: 一键清空所有已保存的图片文件。
"""

import os
from pathlib import Path
from typing import Any
from loguru import logger

# 统一存储路径
MEDIA_DIR = Path("data/images")


def _format_size(size_bytes: int) -> str:
    """字节大小人性化转换"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


class MediaService:
    def __init__(self, media_dir: Path = MEDIA_DIR):
        self.media_dir = media_dir
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        """确保图片存储目录存在"""
        self.media_dir.mkdir(parents=True, exist_ok=True)

    async def save_image(self, img: Any) -> str:
        """
        将生成的 Image 对象安全落地到本地并返回访问 URL。
        如果落地异常，回退至原始 URL。
        """
        self._ensure_dir()
        try:
            if hasattr(img, "save") and callable(img.save):
                saved_path = await img.save(path=str(self.media_dir))
                fname = Path(saved_path).name
                logger.info(f"图片成功落地保存到本地: {fname}")
                return f"/media/images/{fname}"
        except Exception as e:
            logger.warning(f"保存图片至本地失败，回退至原始外链: {e}")

        # 回退原始链接
        return getattr(img, "url", "") or str(img)

    def list_images(self) -> dict[str, Any]:
        """
        获取当前已保存的所有图片列表及统计信息
        """
        self._ensure_dir()
        images = []
        total_size = 0

        # 支持的图片扩展名
        valid_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

        try:
            for entry in os.scandir(self.media_dir):
                if entry.is_file():
                    p = Path(entry.path)
                    if p.suffix.lower() in valid_exts:
                        stat = entry.stat()
                        fsize = stat.st_size
                        total_size += fsize
                        mtime = stat.st_mtime

                        images.append({
                            "filename": p.name,
                            "url": f"/media/images/{p.name}",
                            "size_bytes": fsize,
                            "size_human": _format_size(fsize),
                            "created_at": mtime,
                        })
        except Exception as e:
            logger.error(f"检索本地图片资产失败: {e}")

        # 按创建时间倒序排（最新的在前）
        images.sort(key=lambda x: x["created_at"], reverse=True)

        return {
            "total_count": len(images),
            "total_size_bytes": total_size,
            "total_size_human": _format_size(total_size),
            "images": images,
        }

    def delete_image(self, filename: str) -> bool:
        """
        安全删除指定的单张图片（防御目录穿越）
        """
        self._ensure_dir()
        clean_name = Path(filename).name  # 仅取文件名，防止 ../ 穿越
        target = self.media_dir / clean_name

        if target.is_file():
            try:
                target.unlink()
                logger.info(f"已删除本地图片: {clean_name}")
                return True
            except Exception as e:
                logger.error(f"删除图片 {clean_name} 失败: {e}")
                raise e
        return False

    def clear_all_images(self) -> int:
        """
        一键清空所有图片文件
        """
        self._ensure_dir()
        count = 0
        valid_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

        for entry in os.scandir(self.media_dir):
            if entry.is_file():
                p = Path(entry.path)
                if p.suffix.lower() in valid_exts:
                    try:
                        os.unlink(entry.path)
                        count += 1
                    except Exception as e:
                        logger.warning(f"清理文件 {p.name} 失败: {e}")

        logger.info(f"一键清空完成，共清理 {count} 张本地图片")
        return count


# 全局单例
media_service = MediaService()
