# MyStock — A股情绪周期 RPS 量化系统

基于 tdx2db 写入的 DuckDB 数据，计算个股/板块 RPS 排名、三线红榜单、市场宽度、
资金主力区（成交额）与情绪指标，并提供 Streamlit 可视化。

## 环境要求

- Python 3.10+
- 已有 tdx2db 同步好的 DuckDB 数据库文件

## 安装

在仓库根目录（`MyStock/`）安装依赖：

```bash
pip install -e .
```

## 使用

**所有命令从仓库根目录（`MyStock/`）执行。**

### 初始化历史

```bash
python -m cli.run_daily --db /path/to/your.duckdb --init-history
```

默认从 `raw_kline_daily` 最新日期往前回填 2 年，包含股票池刷新、个股/板块 RPS、
三线红（strict + loose）、市场宽度、资金主力区。

写入采用 **先 DELETE 区间再 INSERT**，重复跑与首次一样快，且会清掉区间内不再满足
条件的过期行。**无需**为了避免残留而每次重建。

### 全新重建（彻底清零）

```bash
python -m cli.run_daily --db /path/to/your.duckdb --init-history --drop-tables
```

先 DROP 所有计算表再全量重算，清掉一切区间外的旧数据。UI「数据管理 → 初始化历史」
走的就是这条。

### 每日更新（收盘后）

```bash
python -m cli.run_daily --db /path/to/your.duckdb
```

不指定 `--date` 则自动取 `raw_kline_daily` 最新日期。每次运行自动刷新股票池
（ST 变更、新股上市当天生效）。

### 补算指定区间

```bash
python -m cli.run_daily --db /path/to/your.duckdb --init-history --start 2026-06-09 --end 2026-06-11
```

`--start/--end` 指定区间重算，不影响区间外历史数据。

### 只重算资金主力区

```bash
python -m cli.run_daily --db /path/to/your.duckdb --only-active
```

改了 `config/thresholds.yaml` 的 `active_pool.fixed_amt_yi` 后，用它只重建
`active_pool_daily`，不跑重的 RPS/三线红步骤。

## 选项

| 选项 | 说明 |
|------|------|
| `--db` | DuckDB 文件路径（必填） |
| `--date` | 指定计算日期，默认取 `raw_kline_daily` 最新日期 |
| `--init-history` | 回填历史，默认从 end_date 往前 2 年 |
| `--start` / `--end` | 配合 `--init-history` 指定区间 |
| `--drop-tables` | 配合 `--init-history`，先 DROP 计算表再重建（彻底清零） |
| `--only-active` | 仅重算 `active_pool_daily` 后退出 |
| `--refresh-blocks` | 仅刷新股票池后退出 |
| `--skip-sanxianhong` | 跳过三线红榜单计算 |
| `--cfg` | 配置文件路径，默认 `config/thresholds.yaml` |

## 输出表

| 表 | 说明 |
|----|------|
| `stock_pool` | 合格股票池（每次运行重建） |
| `rps_stock_daily` | 个股每日 RPS（5/10/20/50/120/250 周期），各周期独立排名 |
| `rps_block_daily` | 板块每日 RPS（5/10/15/20/50 周期） |
| `block_breadth_daily` | 板块市场宽度（NH-NL、High-Low Index、MA20 宽度） |
| `sanxianhong_daily` | 三线红榜单（strict + loose），含连续天数、60日在榜等 |
| `active_threshold_daily` | 每日成交额分布门槛（Pareto50% / 拐点，供分布图与走势） |
| `active_pool_daily` | 资金主力区在榜个股（固定门槛），含连续天数、60日在榜、上榜次数、进退榜 |

## 资金主力区（成交额）

榜单用**固定门槛**（默认日成交额 ≥ 20 亿，配置在 `active_pool.fixed_amt_yi`）——
进出榜只取决于个股自身成交额，标准确定、不受全市场量能波动影响。
Pareto50% / 拐点仅用于对数分布图与门槛走势观察，不参与榜单。

## 股票池过滤规则

每次运行自动重建，排除以下股票：

| 规则 | 说明 |
|------|------|
| 不在 `raw_symbol_name` 中 | 完全退市股（TDX 数据中无名称记录） |
| 代码以 `4` 开头 | 三板 |
| 代码以 `9` 开头 | B股 |

保留 ST 股、退市整理股、北交所（8x）。三线红内部再额外过滤 ST/退。

## 三线红版本

| 版本 | RPS 条件 | 近高比 |
|------|---------|--------|
| `strict` | rps50 **且** rps120 **且** rps250 各自达标 | `high/hhv150` ≥ 0.85 |
| `loose` | rps50 **或** rps120 **或** rps250 **任一**达标 | `high/hhv250` ≥ 0.6 |

阈值在 `config/thresholds.yaml` 调整。

## 可视化

```bash
streamlit run ui/streamlit_app.py -- --db /path/to/your.duckdb
```

模块：三线红榜单、市场宽度、个股 RPS、资金主力区（成交额分布 + 在榜榜单 + K线弹窗）、
数据管理（tdx2db 初始化/更新、本项目初始化历史/每日更新）。

## 参数配置（`config/thresholds.yaml`）

```yaml
sanxianhong:
  strict:
    rps50_min: 90
    rps120_min: 93.1
    rps250_min: 95.1
    hhv_period: 150
    hhv_ratio_min: 0.85
  loose:
    rps_any_min: 95
    hhv_period: 250
    hhv_ratio_min: 0.6
block_rps:
  threshold: 90
  required_count: 3
  preferred_block_type: '概念'
  max_member_count: 100   # 成员超过此数的板块排除出 RPS 排名
active_pool:
  fixed_amt_yi: 20        # 资金主力区固定门槛（亿）
```

## 注意事项

- DuckDB 不支持多进程并发写：JDBC 客户端连接时不能同时运行 Python 写入。
- JDBC 驱动版本需与 Python duckdb 包版本一致，否则报元数据错误。
- `v_stock_qfq` 前复权基准是最新因子，历史价格与通达信显示不同属正常，不影响收益率计算。
