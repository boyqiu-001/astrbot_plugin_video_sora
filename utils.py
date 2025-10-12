import time
import asyncio
from PIL import Image
from io import BytesIO
from curl_cffi import requests, AsyncSession, CurlMime
from astrbot.api import logger

# 轮询参数
max_interval = 60  # 最大间隔
min_interval = 5  # 最小间隔
total_wait = 300  # 最多等待5分钟


class Utils:
    def __init__(self, sora_base_url: str, proxy: str):
        self.sora_base_url = sora_base_url
        self.UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"
        self.proxy = proxy
        proxyes = {"http": proxy, "https": proxy} if proxy else None
        self.session = AsyncSession(impersonate="chrome136", proxies=proxyes)

    async def download_image(self, url: str) -> bytes | None:
        try:
            response = await self.session.get(url)
            return response.content, None
        except (
            requests.exceptions.SSLError,
            requests.exceptions.CertificateVerifyError,
        ):
            # 关闭SSL验证
            response = await self.session.get(url, verify=False)
            return response.content, None
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
            "model": "sy_8",
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

    async def _pending_video(self, task_id: str, authorization: str) -> str | bool:
        try:
            response = await self.session.get(
                self.sora_base_url + "/backend/nf/pending",
                headers={"Authorization": authorization},
            )
            if response.status_code == 200:
                result = response.json()
                for item in result:
                    if item.get("id") == task_id:
                        return item.get("status"), None, item.get("progress_pct")
                return None, None, None  # 任务不存在，视为完成
            else:
                result = response.json()
                err_str = f"视频状态查询失败: {result.get('error', {}).get('message')}"
                logger.error(err_str)
                return "FAILED", err_str, None
        except Exception as e:
            logger.error(f"视频状态查询失败: {e}")
            return "FAILED", "视频状态查询失败", None

    async def pending_video(self, task_id: str, authorization: str) -> bool:
        """轮询等待视频生成完成"""
        interval = max_interval
        elapsed = 0  # 已等待时间
        while elapsed < total_wait:
            status, _, progress = await self._pending_video(task_id, authorization)
            if not status:
                return True, None  # 任务不存在，视为完成
            elif status == "FAILED":
                logger.error("视频生成失败")
                return False, "视频生成失败"
            # 等待当前轮询间隔
            wait_time = min(interval, total_wait - elapsed)
            await asyncio.sleep(wait_time)
            elapsed += wait_time
            # 反向指数退避：间隔逐步减小
            interval = max(min_interval, interval // 2)
            logger.debug(f"视频处理中，{interval}s 后再次请求... 进度: {progress:.2f}%")
        logger.error("视频生成超时")
        return False, "视频生成超时"

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
                            return None, item.get("reason_str")
                        return downloadable_url, None
                return None, "未找到对应的视频"
            else:
                err_str = f"视频链接请求失败: {result.get('error', {}).get('message')}"
                logger.error(err_str)
                return None, err_str
        except Exception as e:
            logger.error(f"视频链接获取失败: {e}")
            return None, "视频链接获取失败"

    async def close(self):
        await self.session.close()
