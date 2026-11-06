# 队员 B 交接说明

2026-11-06。实现范围：`tools/http.py`、`tools/steam_news.py`，以及 B 的测试、真实样本和验证脚本。
保留 Agents SDK 的三个 FunctionTool 名称及参数，供现有 Lead/Patch Historian 直接调用。
没有修改 A/C 工具、编排或前端。默认 `USE_FAKE_TOOLS=1` 仍运行虚构演示；A/C 真实工具完成后再整体切换。

## 已实现的行为

- `search_game`：去空白、校验 limit（1–10）、按 appid 去重、最多补查 15 个候选的详情类型，再返回本体游戏。真实搜索中的 `type=app` 也包括 DLC 和 Toolkit，不能直接据此判断本体。保留多个有效候选供 Lead 澄清。候选验证失败与成功空搜索分别返回错误或空列表。
- `get_game_info`：US 地区、英文详情；发售日可精确到日时转 ISO，否则保留原文；免费与未知价格分别为 `Free`/0 和 null。返回币种、地区、整数最小货币单位、当前折扣、开发商、发行商、类型、平台和 Metacritic。完整依据写入本次证据库。
- `get_patch_notes`：UTC 起止日期均包含，默认只取开发者官方公告。按 gid 去重，分页保留一秒重叠；同秒公告超过页容量时扩大请求，达到预算则声明不完整。官方活动保留 `announcement` 标签；标题或 tags 明示补丁时标为 `patch_notes`。不推断更新导致差评，也不推测内部动机。
- 正文清洗保留段落、列表、链接文字、版本号和否定词，去除 BBCode、HTML、图片和视频占位。`summary` 是最多 1200 字符的原文摘录，不是模型改写；完整文本放在证据库。快照存原始 gid，回放时重新登记 `ref`。

## 共享 HTTP 层供 A/C 使用

```python
from tools.http import get_json, get_text, load_snapshot, save_snapshot, close_client

data = await get_json(url, params)  # 失败抛出异常，具体工具决定如何回退
html = await get_text(url, params)
save_snapshot(appid, "source_name", {"data": data, "as_of": "YYYY-MM-DD"})
snapshot = load_snapshot(appid, "source_name")  # 缺失、损坏或含凭据字段时为 None
# 短命脚本在 finally 中 await close_client()；同一事件循环复用客户端。
```

按真实主机共享异步限流：商店 1.5 秒/请求、SteamSpy 1 秒/请求、Steam API 0.25 秒/请求，SteamCharts 1 秒/请求。
`host_limit` 可省略；兼容 `steam_store`、`steamspy`、`steam_api`、`steamcharts` 别名。
缓存包含 URL、完整规范化参数和响应类型；可选 cache_key 不能覆盖这些维度。成功响应缓存 300 秒，最多 256 项，读取副本避免互相修改。
429、500/502/503/504 和传输错误最多尝试三次；遵守 Retry-After（秒或 HTTP 日期），并把冷却时间共享给同主机调用。
每个 HTTP 操作总预算 35 秒，包含限流和退避。B 的多请求工具另有 65 秒预算，公告最多 8 页。

`save_snapshot` 原子替换文件，保留调用方元数据并补充 UTC `captured_at`、`as_of`。
此通用函数不会替 A/C 包装原始响应；工具应记录查询条件与覆盖范围，避免误用其他地区、日期或筛选条件的快照。
快照仅保存公开数据，禁止嵌套凭据字段；不保存凭据、HTTP 错误响应或模型日志。

## B 快照契约供 C 的脚本使用

直接调用未装饰的原始取数函数：

```python
from tools.steam_news import fetch_game_details, fetch_news_window
from tools.http import save_snapshot

details = await fetch_game_details(appid)
save_snapshot(appid, "appdetails", details)
news = await fetch_news_window(appid, start, end, official_only=True)
if news["coverage"]["complete"]:
    save_snapshot(appid, "news_official", news)
```

包括媒体时用 `official_only=False` 和名称 `news_all`。原始取数函数会抛出异常；脚本应处理网络失败，并在退出前关闭客户端。
三个名称均保存到 `data/snapshots/<appid>/<name>.json`。B 实时工具也会保存成功详情和完整公告窗口，磁盘写入失败只返回警告。

```json
{
  "schema_version": 1,
  "appid": 1086940,
  "source": "<完整端点 URL>",
  "params": {"appid": 1086940, "official_only": true},
  "as_of": "2026-11-06",
  "captured_at": "<UTC ISO 时间>",
  "coverage": {"start": "2026-03-01", "end": "2026-03-31", "complete": true, "reason": "crossed_start"},
  "data": {"appnews": {"appid": 1086940, "newsitems": []}}
}
```

上例仅展示结构。`appdetails` 没有 coverage，其 params 为 `{appids: appid, cc: "US", l: "english"}`，data 为 Steam 完整 appdetails 响应。
新闻快照 params 为 `{appid, official_only}`，只有覆盖完整且包含请求窗口时才可回退；官方快照不能回答包含媒体的完整查询。
`news_all` 可筛选出官方子集。每个名称目前保存一个窗口，新完整查询会替换该名称，演示前应生成覆盖示例日期的较大窗口。
搜索离线回退只查本地详情快照；返回 `partial=true` 和限定搜索范围，不代表 Steam 全库搜索。

## 验收与证据

```powershell
python -m uv --system-certs sync --locked
python -m uv run --locked pytest -q
python -m uv run --locked scripts/check_steam_news.py --start 2026-03-01 --end 2026-03-31
```

本机 uv 不在 PATH，使用 `python -m uv`。首次下载曾超时，使用系统证书后安装成功；没有改动依赖声明或锁文件。
最终完整测试集 44 项全部通过。离线测试覆盖缓存、参数隔离、并发限流、重试预算、原子写入、损坏快照、搜索歧义、DLC、免费/未知价格、同日边界、同秒分页、覆盖不足和新证据编号。

真实接口原始样本在 `data/samples/b/`：每份记录 source、params、UTC captured_at、HTTP status、data。
`search.json` 含本体、DLC、Toolkit；`search_dlc.json` 是真实的成功空搜索。测试中的其他类型、缺失值和分页边界由样本变体或显式合成数据构造。
随仓库提供 BG3 详情、DLC/Toolkit 类型验证详情，以及 2026-03-01 至 2026-03-31 的完整官方公告窗口。

`docs/b_validation.json` 记录现场验收：三个 B 工具全部使用实时数据，随后禁用 JSON HTTP 重放相同查询，检查日期保持采集日、引用登记到新的证据库。
验证脚本还读取原始公告链接的 Steam 页面，解析其 `data-partnereventstore` 公告数据，核对标题、发布日期、正文摘录；没有调用模型或使用 Steam key。

接口参数参考 [Steam 官方 ISteamNews 文档](https://partner.steamgames.com/doc/webapi/ISteamNews?language=english)。商店搜索与详情以本机保存的真实响应验证为准。

## 密钥约束

现有 Steam key 文件保留在仓库外的本地目录，本次开发未读取、复制或发送它。
Git、Docker、Cloud Run 源码上传排除 `.env`、密钥文件和凭据文件；`.env.example` 仅保留空值示例。
本次 B 验收无需任何密钥。完整 agent 的 Gemini/Vertex 权限及 A/C 实时数据尚不在本次验收范围内。
