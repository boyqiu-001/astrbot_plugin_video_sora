# astrbot_plugin_video_sora

基于 Sora（非Sora2） 的视频生成插件。通过文本提示（可选参考图片）生成视频。

## 获取网页鉴权
accessToken获取方式：
登录https://chatgpt.com后，进入https://chatgpt.com/api/auth/session复制accessToken字段的值填写进去即可，头部不需要加Bearer 

## 使用说明
触发词（支持前缀 / # %）：
- 生成视频 [横屏|竖屏] <提示>
- 视频生成 [横屏|竖屏] <提示>
- sora [横屏|竖屏] <提示>

可在消息中直接附图或回复图片作为参考；未提供图片则仅用文本生成。

## 并发控制
- 每个 Authorization 最多并发 2 个请求；无可用 token 时会提示并发过多或未配置。

## 风险
- 本插件基于网页逆向的方式调用官方接口，存在封号风险，请谨慎使用。
- 网络相关错误：检查 proxy 或主机网络访问能力，已知部分国家网络无法访问sora，例如新加坡。
