# astrbot_plugin_video_sora

基于 Sora2 的视频生成插件。支持通过文本和图片生成视频。

## 获取网页鉴权（accessToken）
1. 登录 https://chatgpt.com
2. 打开 https://chatgpt.com/api/auth/session
3. 复制返回内容中的 accessToken 字段（仅 token 值，配置处不需要加 `Bearer ` 前缀）

## 使用说明
- sora [横屏|竖屏] <提示>
- 生成视频 [横屏|竖屏] <提示>
- 视频生成 [横屏|竖屏] <提示>
- [横屏|竖屏] 参数是可选的

可在消息中直接附图或回复图片作为参考；若未提供图片，仅用文本生成。

查询与重试：
- sora查询 <task_id>  
可用来查询任务状态、重放已生成的视频或重试未完成的任务。总之一个命令全搞定。

## 并发控制与错误提示
- 每个 token（Authorization）最多并发 2 个任务；无可用 token 时会提示并发过多或未配置。
- 插件会在数据库（video_data.db）记录任务状态，包含 task_id、prompt、image_url、status、video_url、error_msg 等信息，方便后续查询与排查。

## 故障排查
- 网络相关错误：检查 proxy 或主机网络访问能力，已知部分国家网络无法访问sora，例如新加坡。

## 风险提示
- 本插件基于网页逆向的方式调用官方接口，存在封号风险，请谨慎使用。

## 致谢
- 感谢 [leetanshaj/openai-sentinel](https://github.com/leetanshaj/openai-sentinel) 公开 PoW 算法。（Thanks [leetanshaj/openai-sentinel](https://github.com/leetanshaj/openai-sentinel) for the PoW algorithm.）
