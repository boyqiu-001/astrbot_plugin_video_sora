import re
import random
import asyncio
import astrbot.api.message_components as Comp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api.message_components import Video
from .utils import Utils


class VideoSora(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config  # 读取配置文件
        sora_base_url = self.config.get("sora_base_url", "https://sora.chatgpt.com")
        self.speed_down_url = self.config.get("speed_down_url")
        proxy = self.config.get("proxy")
        self.utils = Utils(sora_base_url, proxy)
        self.auth_dict = dict.fromkeys(self.config.get("authorization_list", []), 0)
        self.screen_mode = self.config.get("screen_mode", "自动")
        self.def_prompt = self.config.get("default_prompt", "")

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    @filter.regex(r"^(?:/|#|%)?\s*(生成视频|视频生成|sora)\s*(横屏|竖屏)?(.*)$")
    async def video_sora(self, event: AstrMessageEvent):
        """使用sora模型生成视频"""
        # 解析参数
        msg = re.match(
            r"^(?:/|#|%)?(生成视频|视频生成|sora)\s*(横屏|竖屏)?(.*)$",
            event.message_str,
        )
        prompt = self.def_prompt
        # 提示词优先取第三组
        if msg.group(3) and msg.group(3).strip():
            prompt = msg.group(3).strip()
        # 如果用户把提示词写在第二组而没有指定横/竖屏
        elif msg.group(2) and msg.group(2) not in ["横屏", "竖屏"]:
            prompt = msg.group(2).strip()

        yield event.plain_result("视频正在生成，请稍等~")

        # 随机选择一个Authorization
        if not self.auth_dict:
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain("请先在插件配置中添加Authorization"),
                ]
            )
            return
        valid_tokens = [k for k, v in self.auth_dict.items() if v < 2]
        if not valid_tokens:
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain("当前并发数过多，请稍后再试"),
                ]
            )
            return
        auth_token = random.choice(valid_tokens)
        authorization = "Bearer " + auth_token
        self.auth_dict[auth_token] += 1  # 并发数+1

        try:
            # 遍历消息链，获取第一张图片
            image_url = ""
            for comp in event.get_messages():
                if isinstance(comp, Comp.Image):
                    image_url = comp.url
                    break
                elif isinstance(comp, Comp.Reply):
                    for quote in comp.chain:
                        if isinstance(quote, Comp.Image):
                            image_url = quote.url
                            break
                    break

            # 下载图片
            image_bytes = None
            if image_url:
                image_bytes = await self.utils.download_image(image_url)
                if not image_bytes:
                    yield event.chain_result(
                        [
                            Comp.Reply(id=event.message_obj.message_id),
                            Comp.Plain("下载失败"),
                        ]
                    )
                    return

            # 竖屏还是横屏
            screen_mode = "portrait"
            if msg.group(2) and msg.group(2) not in ["横屏", "竖屏"]:
                params = msg.group(2).strip()
                screen_mode = "landscape" if params == "横屏" else "portrait"
            elif self.screen_mode in ["横屏", "竖屏"]:
                screen_mode = "landscape" if self.screen_mode == "横屏" else "portrait"
            elif self.screen_mode == "自动" and image_bytes:
                screen_mode = self.utils.get_image_orientation(image_bytes)

            # 如果消息中携带图片，上传图片到OpenAI端点
            images_id = ""
            if image_bytes:
                images_id = await self.utils.upload_images(
                    authorization, image_bytes
                )
                if not images_id:
                    yield event.chain_result(
                        [
                            Comp.Reply(id=event.message_obj.message_id),
                            Comp.Plain("图片上传失败"),
                        ]
                    )
                    return

            # 生成视频
            video_id = await self.utils.create_video(
                prompt, screen_mode, images_id, authorization
            )
            if not video_id:
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain("视频生成失败"),
                    ]
                )
                return

            # 轮询等待视频生成
            result = await self.utils.pending_video(video_id, authorization)
            if not result:
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain("视频生成失败"),
                    ]
                )
                return

            # 获取视频下载地址
            await asyncio.sleep(5)  # 等待5秒
            video_url = await self.utils.fetch_video_url(video_id, authorization)
            if not video_url:
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain("视频下载地址获取失败"),
                    ]
                )
                return

            # 如果配置了加速下载地址，则拼接域名
            if self.speed_down_url:
                video_url = self.speed_down_url + video_url
            yield event.chain_result([Video.fromURL(url=video_url)])
        finally:
            self.auth_dict[auth_token] -= 1

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        self.utils.close()
