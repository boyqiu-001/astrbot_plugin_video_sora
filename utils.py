import ssl
import time
import asyncio
import aiohttp
from PIL import Image
from io import BytesIO
from curl_cffi import requests as req
from astrbot.api import logger

# 轮询参数
max_interval = 60  # 最大间隔
min_interval = 5  # 最小间隔
total_wait = 300  # 最多等待5分钟


class Utils:
    def __init__(self, sora_base_url: str, proxy: str):
        self.sora_base_url = sora_base_url
        self.session = aiohttp.ClientSession()
        self.UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"
        self.proxy = proxy
        self.proxies = {
            "http": proxy,
            "https": proxy,
        }
        self.impersonate = "chrome136"

    async def download_image(self, url: str) -> bytes | None:
        try:
            async with self.session.get(url) as resp:
                return await resp.read()
        except (
            aiohttp.ClientConnectorSSLError,
            aiohttp.ClientConnectorCertificateError,
        ):
            # 关闭SSL验证
            ssl_context = ssl.create_default_context()
            ssl_context.set_ciphers("DEFAULT")
            async with self.session.get(url, ssl=ssl_context) as resp:
                return await resp.read()
        except Exception as e:
            logger.error(f"图片下载失败: {e}")
            return None

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
            files = {
                "file": (f"{int(time.time() * 1000)}.png", image_bytes, "image/png")
            }
            response = req.post(
                self.sora_base_url + "/backend/uploads",
                files=files,
                headers={"Authorization": authorization},
                proxies=self.proxies if self.proxy else None,
                impersonate=self.impersonate,
            )
            result = response.json()
            if response.status_code == 200:
                return result.get("id")
            else:
                logger.error(f"图片上传失败: {result.get('error', {}).get('message')}")
                return None

        except Exception as e:
            logger.error(f"图片上传失败: {e}")
            return None

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
            "model": "sy_8",
            "style_id": None,
            "audio_caption": None,
            "audio_transcript": None,
            "video_caption": None,
            "storyboard_id": None,
        }
        try:
            response = req.post(
                self.sora_base_url + "/backend/nf/create",
                json=payload,
                headers={"Authorization": authorization},
                proxies=self.proxies if self.proxy else None,
                impersonate=self.impersonate,
            )
            try:
                result = response.json()
            except Exception:
                text = response.text
                logger.error(f"解析JSON失败，原始响应内容: {text}")
                return None
            if response.status_code == 200:
                return result.get("id")
            else:
                logger.error(f"视频生成失败: {result.get('error', {}).get('message')}")
                return None
        except Exception as e:
            logger.error(f"视频生成失败: {e}")
            return None

    async def _pending_video(self, task_id: str, authorization: str) -> str | None:
        try:
            response = req.get(
                self.sora_base_url + "/backend/nf/pending",
                headers={"Authorization": authorization},
                proxies=self.proxies if self.proxy else None,
                impersonate=self.impersonate,
            )
            if response.status_code == 200:
                result = response.json()
                for item in result:
                    if item.get("id") == task_id:
                        return item.get("status")
                return None  # 任务不存在，视为完成
            else:
                logger.error(
                    f"视频状态查询失败: {result.get('error', {}).get('message')}"
                )
                return "FAILED"
        except Exception as e:
            logger.error(f"视频状态查询失败: {e}")
            return "FAILED"

    async def pending_video(self, task_id: str, authorization: str) -> bool:
        """轮询等待视频生成完成"""
        interval = max_interval
        elapsed = 0  # 已等待时间
        while elapsed < total_wait:
            status = await self._pending_video(task_id, authorization)
            if not status:
                return True  # 任务不存在，视为完成
            elif status == "FAILED":
                logger.error("视频生成失败")
                return False
            # 等待当前轮询间隔
            wait_time = min(interval, total_wait - elapsed)
            await asyncio.sleep(wait_time)
            elapsed += wait_time
            # 反向指数退避：间隔逐步减小
            interval = max(min_interval, interval // 2)
            logger.debug(f"视频处理中，{interval}s 后再次请求...")
        logger.error("视频生成超时")
        return False

    async def fetch_video_url(self, task_id: str, authorization: str) -> str | None:
        try:
            response = req.get(
                self.sora_base_url + "/backend/project_y/profile/drafts?limit=15",
                headers={"Authorization": authorization},
                proxies=self.proxies if self.proxy else None,
                impersonate=self.impersonate,
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
                            return item.get("reason_str")
                        return downloadable_url
                return None
            else:
                logger.error(
                    f"视频链接请求失败: {result.get('error', {}).get('message')}"
                )
                return None
        except Exception as e:
            logger.error(f"视频链接获取失败: {e}")
            return None

    async def close(self):
        await self.session.close()
