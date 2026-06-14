"""情绪周期指标计算：基于 zt/dt/zbgc 股池 + raw_basic_daily / raw_kline_daily。

写入 sentiment_daily。窗口函数扫全表，按交易日历做 prev/next 映射。
单日与区间共用同一段 SQL，只是 INSERT 的目标日期范围不同，保证一致。

口径说明：
- 收益：昨收→今收。等价于「昨日入池的股票今日的 change_pct 均值」
  （因 change_pct[d] = (close[d]-preclose[d])/preclose[d]，preclose[d]=close[昨]）。
- 涨停封板率 = 涨停家数 / (涨停家数 + 炸板家数)。
- 跌停封板率 = 跌停池中 open_count=0（未开板）占比（东财无独立跌停炸板池）。
- 断板：连板数>=2 的股票，次一交易日不在涨停池。
- 断板风险：以当日为观测日，统计恰好在 D-2 断板的股票（观测窗口固定为3天），
  取各股从断板日到今日的最低价，判断是否低于 (base_price + pre_streak_price) / 2，
  等价于从 base_price 下跌幅度 >= 连板总涨幅的一半。
  pre_streak_price = 连板开始前一日收盘（raw_basic_daily，consecutive=N → 往前 N 格）。
"""
from __future__ import annotations

import duckdb

# 断板后观察窗口（交易日数，含断板当日）
_BREAK_WINDOW = 3


def _build_sql(where_target: str) -> str:
    """组装计算 SQL。where_target 限定最终 INSERT 的 trade_date 范围。"""
    return f"""
INSERT OR REPLACE INTO sentiment_daily
WITH td AS (
    SELECT trade_date,
           ROW_NUMBER() OVER (ORDER BY trade_date) AS idx
    FROM (SELECT DISTINCT date AS trade_date FROM raw_basic_daily)
),
zt_cnt AS (
    SELECT trade_date,
           COUNT(*)                              AS zt_count,
           MAX(consecutive)                      AS max_consecutive,
           COUNT(*) FILTER (WHERE consecutive >= 2) AS lianban_count
    FROM zt_pool_daily GROUP BY trade_date
),
dt_cnt AS (
    SELECT trade_date,
           COUNT(*)                              AS dt_count,
           COUNT(*) FILTER (WHERE open_count = 0) AS dt_sealed
    FROM dt_pool_daily GROUP BY trade_date
),
zb_cnt AS (
    SELECT trade_date, COUNT(*) AS zbgc_count
    FROM zbgc_pool_daily GROUP BY trade_date
),
-- 昨日涨停/连板 → 次日收益（按次日 change_pct 聚合）
-- 用 td 映射下一交易日，同时要求 zt 日期本身也在 td 里（raw_basic_daily 覆盖范围内）
zt_ret AS (
    SELECT nxt.trade_date AS trade_date,
           AVG(rb.change_pct)                                   AS prev_zt_return,
           AVG(rb.change_pct) FILTER (WHERE z.consecutive >= 2) AS prev_lianban_return
    FROM zt_pool_daily z
    JOIN td cur ON cur.trade_date = z.trade_date
    JOIN td nxt ON nxt.idx = cur.idx + 1
    JOIN raw_basic_daily rb ON rb.symbol = z.symbol AND rb.date = nxt.trade_date
    GROUP BY nxt.trade_date
),
-- 昨日炸板 → 次日收益
zb_ret AS (
    SELECT nxt.trade_date AS trade_date,
           AVG(rb.change_pct) AS prev_zbgc_return
    FROM zbgc_pool_daily zb
    JOIN td cur ON cur.trade_date = zb.trade_date
    JOIN td nxt ON nxt.idx = cur.idx + 1
    JOIN raw_basic_daily rb ON rb.symbol = zb.symbol AND rb.date = nxt.trade_date
    GROUP BY nxt.trade_date
),
-- 断板事件：连板>=2 且次日不在涨停池；同时取连板开始前一日收盘作为涨幅基准
breaks AS (
    SELECT z.symbol,
           nxt.trade_date  AS break_date,
           nxt.idx         AS break_idx,
           z.close         AS base_price,
           rb_pre.close    AS pre_streak_price
    FROM zt_pool_daily z
    JOIN td cur ON cur.trade_date = z.trade_date
    JOIN td nxt ON nxt.idx = cur.idx + 1
    -- consecutive=N → 连板前一日在 td 里往回 N 格
    JOIN td pre ON pre.idx = cur.idx - z.consecutive
    JOIN raw_basic_daily rb_pre ON rb_pre.symbol = z.symbol AND rb_pre.date = pre.trade_date
    LEFT JOIN zt_pool_daily z2
           ON z2.symbol = z.symbol AND z2.trade_date = nxt.trade_date
    WHERE z.consecutive >= 2 AND z2.symbol IS NULL
),
-- break_obs：以观测日 obs_date 为基准，收集过去 BREAK_WINDOW 天内发生的断板事件
break_obs AS (
    SELECT obs.trade_date AS obs_date,
           obs.idx        AS obs_idx,
           b.symbol,
           b.break_idx,
           b.base_price,
           b.pre_streak_price
    FROM breaks b
    JOIN td obs ON obs.idx = b.break_idx + 2
),
-- 对每个 (obs_date, 断板事件)，取断板日到 obs_date 的最低价
break_low AS (
    SELECT bo.obs_date AS trade_date,
           bo.symbol,
           bo.base_price,
           bo.pre_streak_price,
           MIN(k.low) AS min_low
    FROM break_obs bo
    JOIN td w ON w.idx BETWEEN bo.break_idx AND bo.obs_idx
    JOIN raw_kline_daily k ON k.symbol = bo.symbol AND k.date = w.trade_date
    GROUP BY bo.obs_date, bo.symbol, bo.base_price, bo.pre_streak_price
),
-- 断板风险：min_low < (base_price + pre_streak_price) / 2
-- 等价于：跌幅 >= 连板总涨幅的一半
break_agg AS (
    SELECT trade_date,
           COUNT(*)                                                                  AS break_count,
           COUNT(*) FILTER (WHERE min_low < (base_price + pre_streak_price) / 2.0) AS break_risk_count
    FROM break_low
    GROUP BY trade_date
)
SELECT
    td.trade_date,
    COALESCE(zt_cnt.zt_count, 0)        AS zt_count,
    COALESCE(dt_cnt.dt_count, 0)        AS dt_count,
    COALESCE(zb_cnt.zbgc_count, 0)      AS zbgc_count,
    CASE WHEN COALESCE(zt_cnt.zt_count,0) + COALESCE(zb_cnt.zbgc_count,0) > 0
         THEN zt_cnt.zt_count::DOUBLE
              / (COALESCE(zt_cnt.zt_count,0) + COALESCE(zb_cnt.zbgc_count,0))
         END                            AS zt_seal_rate,
    CASE WHEN COALESCE(dt_cnt.dt_count,0) > 0
         THEN dt_cnt.dt_sealed::DOUBLE / dt_cnt.dt_count
         END                            AS dt_seal_rate,
    zt_cnt.max_consecutive,
    COALESCE(zt_cnt.lianban_count, 0)   AS lianban_count,
    zt_ret.prev_zt_return,
    zt_ret.prev_lianban_return,
    zb_ret.prev_zbgc_return,
    break_agg.break_count,
    break_agg.break_risk_count,
    CASE WHEN break_agg.break_count > 0
         THEN break_agg.break_risk_count::DOUBLE / break_agg.break_count
         END                            AS break_risk_ratio
FROM td
LEFT JOIN zt_cnt    ON zt_cnt.trade_date    = td.trade_date
LEFT JOIN dt_cnt    ON dt_cnt.trade_date    = td.trade_date
LEFT JOIN zb_cnt    ON zb_cnt.trade_date    = td.trade_date
LEFT JOIN zt_ret    ON zt_ret.trade_date    = td.trade_date
LEFT JOIN zb_ret    ON zb_ret.trade_date    = td.trade_date
LEFT JOIN break_agg ON break_agg.trade_date = td.trade_date
WHERE {where_target}
"""


def calc_sentiment_history(
    con: duckdb.DuckDBPyConnection, start_date: str, end_date: str
) -> int:
    """计算区间内每日情绪指标。窗口扫全表，仅写入 [start, end]。"""
    sql = _build_sql("td.trade_date BETWEEN $start_date AND $end_date")
    con.execute(sql, {"start_date": start_date, "end_date": end_date})
    row = con.execute(
        "SELECT COUNT(*) FROM sentiment_daily WHERE trade_date BETWEEN $s AND $e",
        {"s": start_date, "e": end_date},
    ).fetchone()
    return row[0] if row else 0


def calc_sentiment(con: duckdb.DuckDBPyConnection, target_date: str) -> int:
    """单日计算 = 区间计算的子集，保证与回填一致。"""
    return calc_sentiment_history(con, target_date, target_date)
