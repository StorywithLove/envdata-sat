# envdata-sat

中国 31 个省会/直辖市行政中心的**卫星短波辐射（GHI）**采集与可视化：每小时抓取 Himawari-9 原生 10 分钟辐射数据，按日归档为 CSV，并在 GitHub Pages 上提供一张可交互看板。

- 看板：<https://storywithlove.github.io/envdata-sat/>
- 数据源：Open-Meteo Satellite Radiation API（`models=jma_jaxa_himawari`，`temporal_resolution=native`）
- 数据出口：`data/daily/YYYY/MM/radiation_YYYYMMDD.csv`（权威历史）+ `data/cities/latest.json`（滚动 24 小时快照）

## 仓库里有什么

| 路径 | 作用 |
|---|---|
| `config/cities.json` | 31 个固定站点（省会 / 直辖市）的 id、名称、经纬度 |
| `src/radiation_extract.py` | 单次抓取：固定站点单日、或任意经纬度 CSV + 日期区间 |
| `src/radiation_pipeline.py` | 每小时增量采集：抓当天（必要时补昨天）→ 合并去重 → 写 CSV 与滚动快照 |
| `src/build_hourly_grid.py` | 可选实验：从 JAXA P-Tree L2 扫描生成中国区小时级栅格，不在主链路里 |
| `src/probe_satellite_api.py` | 诊断脚本：对比 archive / forecast 等端点组合的返回形态 |
| `web/` | 看板（Leaflet + Chart.js，纯静态） |
| `api/main.py` | 只读 FastAPI，读 `latest.json` 暴露 `/api/v1/cities`、`/api/v1/radiation/point` |
| `data/daily/` | 按 UTC 日归档的 CSV + 同名 `.status.json` 质量报告 |

## 数据语义

Open-Meteo 的 Himawari `shortwave_radiation` 是**后向平均区间值**。UTC 日 D 的一个 CSV 有 144 个区间端点标签，从 `D 00:10` 到 `D+1 00:00`；其中 `D+1 00:00` 这个标签代表 `D 23:50–D+1 00:00` 这一段。

所以单日请求需要把 `end_date` 多要一天才能取到 `24:00` 端点，但 API 不允许 `end_date` 晚于今天（会返回 400 `out of allowed range`）。采集器因此把结束日期钳制在当前 UTC 日：查今天时拿不到 `24:00` 那个点，查昨天时能拿到，这是有意的。

缺测就是缺测：不插值、不填零、不把夜间缺测伪装成 `0 W/m²`。

- `provider_returned_null` — API 返回了这个时间戳，但值是 null
- `provider_timestamp_missing` — API 干脆没返回这个时间点

两者都不能证明卫星故障。请求本身失败会单独记在位置的 `failures` 里。

## 上游延迟与看板窗口

Himawari 归档的实际延迟远大于官方文档写的 30 分钟。实测在 2026-11-20 UTC 上午，当天数据整列为 null，最后一个有效值停在前一天 23:00。因此：

- **整天全 null 不落盘**。否则 144 行齐全会被判定为「已完成」，这一天以后就再也不会回补。
- 看板的「最近 24 小时」窗口锚定到**最新可用数据**，而不是当前时间，并输出 `window_start_utc` / `window_end_utc` / `data_lag_hours`，避免把只有 15 小时的曲线标成 24 小时。
- 一轮下来什么都没刷新、且请求本身也没报错 → 保留数据、打 `::warning::` 放行（上游滞后不是代码故障）；只有请求真的失败才 `exit 1`。

## 本地运行

需要 Python 3.11+ 和 `requests`：

```bash
pip install requests

# 固定站点，抓某一天
python src/radiation_extract.py cities --date 2026-11-19

# 任意坐标 + 日期区间
python src/radiation_extract.py custom \
  --coordinates-csv config/custom_locations.example.csv \
  --start-date 2026-11-01 --end-date 2026-11-20 \
  --output output/custom_radiation.csv

# 每小时增量采集（工作流跑的就是这一条）
python src/radiation_pipeline.py
```

跑测试：

```bash
python -m unittest discover -s tests -v
```

## 工作流与调度

| 工作流 | 触发 |
|---|---|
| `realtime.yml` | 仅 `workflow_dispatch`，**外部 cron 每小时 :05 调用** |
| `pages.yml` | push 触及 `web/**` 或 `data/cities/**` 时部署看板 |
| `unit-tests.yml` / `test-radiation.yml` | push / PR |
| `hourly-grid.yml` | 手动，JAXA L2 小时栅格实验 |
| `custom-extract-smoke.yml` / `probe-live-api.yml` | 手动诊断 |

`realtime.yml` 没有写 `schedule:` 是有原因的：GitHub 自带的 cron 会延迟甚至漏触发。外部触发方式是在一台常开的机器上（本项目用的是 OCI 实例）用 crontab 调 GitHub 的 dispatch 接口：

```bash
5 * * * * python github_trigger.py envdata-sat realtime.yml
# → POST https://api.github.com/repos/{owner}/{repo}/actions/workflows/realtime.yml/dispatches
#   {"ref":"main"}，带 classic PAT（scope 需含 repo + workflow）
```

## 存储

`data/daily/` 直接提交在本仓库里，是权威数据。没有外部归档仓库、没有转存握手、也不会自动清理历史文件；从工作区删掉某个文件不会抹掉 Git 历史里的 blob（本仓库不做历史重写）。

## 部署看板

`web/` 是纯静态站点，Pages 工作流会把 `data/cities/latest.json` 复制成 `web/data/latest.json` 后上传。需要 **Settings → Pages → Source → GitHub Actions**。

底图用**天地图**（矢量 / 影像 / 地形三选一），因为 OpenStreetMap、Esri 等底图的中国边界绘制不准确。天地图 key 是浏览器端 key，前端可见，靠控制台的域名白名单保护；key 失效或超额时自动回落到高德瓦片。

## 已知限制

- 上游数据源有数小时到一天的滞后，看板显示的永远是「最新可用」，不是「此刻」。
- 天地图矢量底图只覆盖中国；国外区域要用「天地图·影像」（约 z12 以上无影像）。
- `build_hourly_grid.py` 依赖 JAXA P-Tree FTP，该入口目前从常用网络不可达，未接入主链路。
