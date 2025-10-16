import re
import random
import asyncio
import aiosqlite
import os
import astrbot.api.message_components as Comp
from datetime import datetime
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.message_components import Video
from .utils import Utils


# 获取视频下载地址
max_wait = 30  # 最大等待时间（秒）
interval = 3  # 每次轮询间隔（秒）


class VideoSora(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config  # 读取配置文件
        sora_base_url = self.config.get("sora_base_url", "https://sora.chatgpt.com")
        chatgpt_base_url = self.config.get("chatgpt_base_url", "https://chatgpt.com")
        proxy = self.config.get("proxy")
        model = self.config.get("model", "sy_8")
        self.utils = Utils(sora_base_url, chatgpt_base_url, proxy, model)
        self.auth_dict = dict.fromkeys(self.config.get("authorization_list", []), 0)
        self.screen_mode = self.config.get("screen_mode", "自动")
        self.def_prompt = self.config.get("default_prompt", "让图片画面动起来")
        self.speed_down_url_type = self.config.get("speed_down_url_type")
        self.speed_down_url = self.config.get("speed_down_url")
        self.polling_task = set()
        self.task_limit = self.config.get("task_limit", 3)
        self.white_list_enabled = self.config.get("white_list_enabled", False)
        self.white_list = self.config.get("white_list", [])
    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        video_db_path = os.path.join(
            StarTools.get_data_dir("astrbot_plugin_video_sora"), "video_data.db"
        )
        # 打开持久化连接
        self.conn = await aiosqlite.connect(video_db_path)
        self.cursor = await self.conn.cursor()
        await self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS video_data (
                task_id TEXT PRIMARY KEY NOT NULL,
                user_id INTEGER,
                nickname TEXT,
                prompt TEXT,
                image_url TEXT,
                status TEXT,
                video_url TEXT,
                generation_id TEXT,
                message_id INTEGER,
                auth_xor TEXT,
                error_msg TEXT,
                updated_at DATETIME,
                created_at DATETIME
            )
        """)
        await self.conn.commit()

    async def quote_task(
        self, event: AstrMessageEvent, task_id: str, authorization: str, is_check=False
    ) -> tuple[str | None, str | None]:
        """完成视频生成并发送视频"""

        # 检查是否已经有相同任务在处理
        if task_id in self.polling_task:
            status, _, progress = await self.utils.pending_video(task_id, authorization)
            return (
                None,
                f"任务还在队列中，请稍后再看~\n状态：{status} 进度: {progress * 100:.2f}%",
            )
        # 优化人机交互
        if is_check:
            status, err, progress = await self.utils.pending_video(
                task_id, authorization
            )
            if err:
                return None, err
            if status != "Done":
                await event.send(
                    event.chain_result(
                        [
                            Comp.Reply(id=event.message_obj.message_id),
                            Comp.Plain(
                                f"任务还在队列中，请稍后再看~\n状态：{status} 进度: {progress * 100:.2f}%"
                            ),
                        ]
                    )
                )
        self.polling_task.add(task_id)
        try:
            # 等待视频生成
            result, err = await self.utils.poll_pending_video(task_id, authorization)

            # 更新任务进度
            await self.cursor.execute(
                """
                UPDATE video_data SET status = ?, error_msg = ?, updated_at = ? WHERE task_id = ?
            """,
                (
                    result,
                    err,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    task_id,
                ),  # "Done"表示任务队列状态结束，至于任务是否完成，不知道
            )
            await self.conn.commit()

            if result != "Done" or err:
                return None, err

            elapsed = 0
            status = "Done"
            video_url = ""
            generation_id = None
            err = None
            # 获取视频下载地址
            while elapsed < max_wait:
                (
                    status,
                    video_url,
                    generation_id,
                    err,
                ) = await self.utils.fetch_video_url(
                    task_id, authorization, 30 if is_check else 15
                )
                if video_url or status == "Failed":
                    break
                await asyncio.sleep(interval)
                elapsed += interval
            if not video_url and not err:
                status = "Timeout"
                err = "获取视频下载地址超时"
                logger.error(err)

            # 更新任务进度
            await self.cursor.execute(
                """
                UPDATE video_data SET status = ?, video_url = ?, generation_id = ?, error_msg = ?, updated_at = ? WHERE task_id = ?
            """,
                (
                    status,
                    video_url,
                    generation_id,
                    err,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    task_id,
                ),
            )
            await self.conn.commit()

            if not video_url or err:
                return None, err or "生成视频超时"

            if self.speed_down_url:
                if self.speed_down_url_type == "拼接":
                    video_url = self.speed_down_url + video_url
                elif self.speed_down_url_type == "替换":
                    # 替换域名部分
                    video_url = re.sub(
                        r"^(https?://[^/]+)", self.speed_down_url.rstrip("/"), video_url
                    )
            return video_url, None
        finally:
            self.polling_task.remove(task_id)

    async def create_video(
        self,
        event: AstrMessageEvent,
        image_url: str,
        image_bytes: bytes | None,
        prompt: str,
        screen_mode: str,
        authorization: str,
    ) -> str | None:
        """创建视频生成任务"""
        # 如果消息中携带图片，上传图片到OpenAI端点
        images_id = ""
        if image_bytes:
            images_id, err = await self.utils.upload_images(authorization, image_bytes)
            if not images_id or err:
                return None, err

        # 生成视频
        task_id, err = await self.utils.create_video(
            prompt, screen_mode, images_id, authorization
        )
        if not task_id or err:
            return None, err

        # 记录任务数据
        datetime_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.cursor.execute(
            """
            INSERT INTO video_data (task_id, user_id, nickname, prompt, image_url, status, message_id, auth_xor, updated_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                event.message_obj.sender.user_id,
                event.message_obj.sender.nickname,
                prompt,
                image_url,
                "Queued",
                event.message_obj.message_id,
                authorization[-8:],  # 只存储token的最后8位以作区分
                datetime_now,
                datetime_now,
            ),
        )
        await self.conn.commit()
        # 返回结果
        return task_id, None

    @filter.command("sora", alias={"生成视频", "视频生成"})
    async def video_sora(self, event: AstrMessageEvent):
        """使用sora模型生成视频"""
        # 先检测AccessToken是否存在
        if not self.auth_dict:
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain("请先在插件配置中添加Authorization"),
                ]
            )
            return
        if self.white_list_enabled:
            session_id = event.unified_msg_origin
            if session_id not in self.white_list:
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain("您没有权限使用该插件,请联系管理员添加sid白名单"),
                    ]
                )
                return
        # 解析参数
        msg = re.match(
            r"^(?:生成视频|视频生成|sora) (横屏|竖屏)?([\s\S]*)$",
            event.message_str,
        )
        # 提取提示词
        prompt = msg.group(2).strip() if msg and msg.group(2) else self.def_prompt

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
            image_bytes, err = await self.utils.download_image(image_url)
            if not image_bytes or err:
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain(err),
                    ]
                )
                return

        # 竖屏还是横屏
        screen_mode = "portrait"
        if msg.group(1):
            params = msg.group(1).strip()
            screen_mode = "landscape" if params == "横屏" else "portrait"
        elif self.screen_mode in ["横屏", "竖屏"]:
            screen_mode = "landscape" if self.screen_mode == "横屏" else "portrait"
        elif self.screen_mode == "自动" and image_bytes:
            screen_mode = self.utils.get_image_orientation(image_bytes)

        # 随机选择一个Authorization
        valid_tokens = [k for k, v in self.auth_dict.items() if v < 2]
        if not valid_tokens:
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain("当前并发数过多，请稍后再试"),
                ]
            )
            return

        task_id = None
        auth_token = None
        authorization = None
        err = None

        # 打乱顺序，避免请求过于集中
        random.shuffle(valid_tokens)
        # 尝试循环使用所有可用 token，
        for auth_token in valid_tokens:
            authorization = "Bearer " + auth_token
            # 调用创建视频的函数
            task_id, err = await self.create_video(
                event, image_url, image_bytes, prompt, screen_mode, authorization
            )
            # 如果成功拿到 task_id，则跳出循环
            if task_id:
                # 回复用户
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain(f"视频正在生成，请稍等~\nID: {task_id}"),
                    ]
                )
                break

        # 尝试完全部 token 仍然请求失败
        if not task_id:
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(err),
                ]
            )
            return

        try:
            # 记录并发
            if self.auth_dict[auth_token] >= self.task_limit:
                self.auth_dict[auth_token] = self.task_limit
                logger.warning(f"Token {auth_token[-4:]} 并发数已达上限，但仍尝试使用")
            else:
                self.auth_dict[auth_token] += 1

            # 剩下的任务交给quote_task处理
            video_url, msg = await self.quote_task(event, task_id, authorization)
            if not video_url:
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain(msg),
                    ]
                )
                return
            yield event.chain_result([Video.fromURL(url=video_url)])

        finally:
            if self.auth_dict[auth_token] <= 0:
                self.auth_dict[auth_token] = 0
                logger.warning(f"Token {auth_token[-4:]} 并发数计算错误，已重置为0")
            else:
                self.auth_dict[auth_token] -= 1

    @filter.command("sora查询")
    async def check_video_task(self, event: AstrMessageEvent, task_id: str):
        """重放过去生成的视频，或者查询视频生成状态以及重试未完成的生成任务"""
        if self.white_list_enabled:
            session_id = event.unified_msg_origin
            if session_id not in self.white_list:
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain("您没有权限使用该插件,请联系管理员添加sid白名单"),
                    ]
                )
                return
        await self.cursor.execute(
            "SELECT status, video_url, error_msg, auth_xor FROM video_data WHERE task_id = ?",
            (task_id,),
        )
        row = await self.cursor.fetchone()
        if not row:
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain("未找到对应的视频任务"),
                ]
            )
            return
        status, video_url, error_msg, auth_xor = row
        # 先处理错误
        if status == "Failed":
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(error_msg or "视频生成失败"),
                ]
            )
            return
        # 有视频，直接发送视频
        if video_url:
            if self.speed_down_url:
                video_url = self.speed_down_url + video_url
            yield event.chain_result([Video.fromURL(url=video_url)])
            return
        # 再次尝试完成视频生成
        if status == "Queued" or status == "Timeout" or status == "EXCEPTION":
            # 尝试匹配auth_token
            auth_token = None
            for token in self.auth_dict.keys():
                if token.endswith(auth_xor):
                    auth_token = token
                    break
            if not auth_token:
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain("Token不存在，无法查询视频生成状态"),
                    ]
                )
                return
            # 交给quote_task处理
            authorization = "Bearer " + auth_token
            video_url, msg = await self.quote_task(
                event, task_id, authorization, is_check=True
            )
            if not video_url:
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain(msg),
                    ]
                )
                return
            yield event.chain_result([Video.fromURL(url=video_url)])

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        await self.utils.close()
        await self.conn.commit()
        await self.cursor.close()
        await self.conn.close()
