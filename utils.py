import time
import asyncio
from PIL import Image
from io import BytesIO
from curl_cffi import requests, AsyncSession, CurlMime
from astrbot.api import logger

# 轮询参数
max_interval = 60  # 最大间隔
min_interval = 5  # 最小间隔
total_wait = 360  # 最多等待6分钟


class Utils:
    def __init__(self, sora_base_url: str, proxy: str, model: str):
        self.sora_base_url = sora_base_url
        proxyes = {"http": proxy, "https": proxy} if proxy else None
        self.session = AsyncSession(impersonate="chrome136", proxies=proxyes)
        self.model = model

    async def _handle_image(self, image_bytes: bytes) -> bytes | None:
        try:
            with Image.open(BytesIO(image_bytes)) as img:
                # 如果不是 GIF，直接返回原图
                if img.format != "GIF":
                    return image_bytes
                # 处理 GIF
                buf = BytesIO()
                # 判断是否为动画 GIF（多帧）
                if getattr(img, "is_animated", False) and img.n_frames > 1:
                    img.seek(0)  # 只取第一帧
                # 单帧 GIF 或者多帧 GIF 的第一帧都走下面的保存逻辑
                img = img.convert("RGBA")
                img.save(buf, format="PNG")
                return buf.getvalue()
        except Exception as e:
            logger.warning(f"GIF 处理失败，返回原图: {e}")
            return image_bytes

    async def download_image(self, url: str) -> bytes | None:
        try:
            response = await self.session.get(url)
            content = await self._handle_image(response.content)
            return content, None
        except (
            requests.exceptions.SSLError,
            requests.exceptions.CertificateVerifyError,
        ):
            # 关闭SSL验证
            response = await self.session.get(url, verify=False)
            content = await self._handle_image(response.content)
            return content, None
        except Exception as e:
            logger.error(f"图片下载失败: {e}")
            return None, "图片下载失败"

    def get_image_orientation(self, image_bytes: bytes) -> str:
        # 把 bytes 转成图片对象
        img = Image.open(BytesIO(image_bytes))

        width, height = img.size
        if width > height:
            return "landscape"
        elif width < height:
            return "portrait"
        else:
            return "portrait"

    async def upload_images(self, authorization: str, image_bytes: bytes) -> str | None:
        try:
            mp = CurlMime()
            mp.addpart(
                name="file",
                filename=f"{int(time.time() * 1000)}.png",
                content_type="image/png",
                data=image_bytes,
            )
            response = await self.session.post(
                self.sora_base_url + "/backend/uploads",
                multipart=mp,
                headers={"Authorization": authorization},
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("id"), None
            else:
                result = response.json()
                err_str = f"图片上传失败: {result.get('error', {}).get('message')}"
                logger.error(err_str)
                return None, err_str
        except Exception as e:
            logger.error(f"图片上传失败: {e}")
            return None, "图片上传失败"
        finally:
            mp.close()

    async def create_video(
        self, prompt: str, screen_mode: str, image_id: str, authorization: str
    ) -> str | None:
        inpaint_items = [{"kind": "upload", "upload_id": image_id}] if image_id else []
        payload = {
            "kind": "video",
            "prompt": prompt,
            "title": None,
            "orientation": screen_mode,
            "size": "small",
            "n_frames": 300,
            "inpaint_items": inpaint_items,
            "remix_target_id": None,
            "cameo_ids": None,
            "cameo_replacements": None,
            "model": self.model,
            "style_id": None,
            "audio_caption": None,
            "audio_transcript": None,
            "video_caption": None,
            "storyboard_id": None,
        }
        try:
            response = await self.session.post(
                self.sora_base_url + "/backend/nf/create",
                json=payload,
                headers={"Authorization": authorization},
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("id"), None
            else:
                result = response.json()
                err_str = f"视频生成失败: {result.get('error', {}).get('message')}"
                logger.error(err_str)
                return None, err_str
        except Exception as e:
            logger.error(f"视频生成失败: {e}")
            return None, "视频生成失败"

    async def pending_video(self, task_id: str, authorization: str) -> str | bool:
        try:
            response = await self.session.get(
                self.sora_base_url + "/backend/nf/pending",
                headers={"Authorization": authorization},
            )
            if response.status_code == 200:
                result = response.json()
                for item in result:
                    if item.get("id") == task_id:
                        return item.get("status"), None, item.get("progress_pct") or 0
                return "Done", None, 0  # 任务不存在，视为完成
            else:
                result = response.json()
                err_str = f"视频状态查询失败: {result.get('error', {}).get('message')}"
                logger.error(err_str)
                return "Failed", err_str, 0
        except Exception as e:
            logger.error(f"视频状态查询失败: {e}")
            return "EXCEPTION", "视频状态查询失败", 0

    async def poll_pending_video(
        self, task_id: str, authorization: str
    ) -> bool | str | None:
        """轮询等待视频生成完成"""
        interval = max_interval
        elapsed = 0  # 已等待时间
        while elapsed < total_wait:
            status, err, progress = await self.pending_video(task_id, authorization)
            if status == "Done":
                return "Done", None  # 任务不存在，视为完成
            elif status == "Failed":
                logger.error("视频状态查询失败")
                return (
                    "Failed",
                    f"视频状态查询失败，ID: {task_id}，进度: {progress * 100:.2f}%，错误: {err}",
                )
            elif status == "EXCEPTION":
                logger.error("视频状态查询异常")
                return (
                    "EXCEPTION",
                    f"视频状态查询异常，ID: {task_id}，进度: {progress * 100:.2f}%",
                )
            # 等待当前轮询间隔
            wait_time = min(interval, total_wait - elapsed)
            await asyncio.sleep(wait_time)
            elapsed += wait_time
            # 反向指数退避：间隔逐步减小
            interval = max(min_interval, interval // 2)
            logger.debug(
                f"视频处理中，{interval}s 后再次请求... 进度: {progress * 100:.2f}%"
            )
        logger.error("视频状态查询超时")
        return (
            "Timeout",
            f"视频状态查询超时，ID: {task_id}，生成进度: {progress * 100:.2f}%",
        )

    async def fetch_video_url(self, task_id: str, authorization: str) -> str | None:
        try:
            response = await self.session.get(
                self.sora_base_url + "/backend/project_y/profile/drafts?limit=15",
                headers={"Authorization": authorization},
            )
            result = response.json()
            if response.status_code == 200:
                for item in result.get("items", []):
                    if item.get("task_id") == task_id:
                        downloadable_url = item.get("downloadable_url")
                        if not downloadable_url:
                            logger.error(
                                f"视频链接为空, task_id: {task_id}, reason: {item.get('reason_str')}"
                            )
                            return (
                                "Failed",
                                None,
                                item.get("id"),
                                item.get("reason_str"),
                            )
                        return "Done", downloadable_url, item.get("id"), None
                return "EXCEPTION", None, None, "未找到对应的视频"
            else:
                err_str = f"视频链接请求失败: {result.get('error', {}).get('message')}"
                logger.error(err_str)
                return "Failed", None, None, err_str
        except Exception as e:
            logger.error(f"视频链接获取失败: {e}")
            return "EXCEPTION", None, None, "视频链接获取失败"

    async def close(self):
        await self.session.close()
