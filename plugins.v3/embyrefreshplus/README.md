# Emby精准刷新增强（EmbyRefreshPlus）

MoviePilot V3 插件：监听 `EventType.TransferComplete`，收集整理后的目标路径，忽略指定目录，并在延迟防抖后尝试仅刷新对应 Emby 目录项目。插件不调用 Emby 全库扫描接口。

## 安装/开发

此目录为 V3 插件源格式。将 `plugins.v3/embyrefreshplus/` 放入本地插件仓库并在 `PLUGIN_LOCAL_REPO_PATHS` 配置仓库根目录，或按 MoviePilot 插件市场/仓库发布流程发布。默认关闭；安装后需在插件设置中启用并选择 Emby 实例。

## 配置

- 启用插件：默认关闭
- Emby 媒体服务器：选择 MoviePilot 中已配置且启用的 Emby 服务
- 刷新延迟：默认 10 秒，0–300 秒；连续整理事件会重置计时并合并路径
- 忽略路径：默认 `/media-115-CD2` 和 `/media-115-strm`，每行一个；路径本身及子路径都会跳过

## 刷新方式和限制

Emby 的公开 REST 接口通常提供 `POST /emby/Items/{Id}/Refresh`（递归刷新项目），而不是通用的 `RefreshItems(path)`。
插件将目标路径与 Emby 实例的可选媒体文件夹路径比较，取最深层匹配目录，然后刷新其 Emby item ID。若没有精确匹配，记录警告并跳过，**不会退化为全库刷新**。若 Emby 与 MP 看到的容器路径不同且无法匹配，插件会安全跳过；该情况下需要将映射路径纳入等价映射功能（本版本尚未实现），而不是启用全库扫描。

## 日志

日志以 `[EmbyRefreshPlus]` 开头，包含整理事件、忽略判断、匹配媒体库目录及请求成功/失败。Emby 接受项目刷新请求不等于其已完成媒体识别/入库；仍需查看 Emby 任务和服务器日志确认结果。

## 兼容说明

代码参考 MoviePilot V3 的插件 API 和当前内置 Emby 模块。`RefreshItems(path)` 在当前检查到的内置 Emby 适配器中并非公开方法；插件使用 Emby item-scoped Refresh REST endpoint。若宿主 API 或 Emby 版本差异导致接口不兼容，请先在测试环境验证。
