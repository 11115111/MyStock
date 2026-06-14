"""Stock and block RPS calculation.

Data flow:
  [static caches, refreshed on data sync]
    stock_pool          ← refresh_stock_pool()

  [daily cache, refreshed once per trading day]
    block_daily_pct     ← calc_block_daily_pct() / calc_block_daily_pct_history()

  [final outputs]
    rps_stock_daily     ← calc_stock_rps() / calc_stock_rps_history()
    rps_block_daily     ← calc_block_rps() / calc_block_rps_history()
    block_breadth_daily ← calc_block_breadth() / calc_block_breadth_history()
                          (reads per-stock flags from rps_stock_daily, run after it)
"""
from __future__ import annotations

import duckdb

# ---------------------------------------------------------------------------
# Step 1a: block_daily_pct — expensive JOIN done once per day
# ---------------------------------------------------------------------------

_SQL_BLOCK_DAILY_PCT_SINGLE = """
INSERT OR REPLACE INTO block_daily_pct
SELECT
    bd.date                                                                  AS trade_date,
    bm.block_code,
    bi.block_name,
    bi.block_type,
    SUM(CASE WHEN bd.turnover > 0 THEN bd.change_pct * bd.floatmv END)
        / NULLIF(SUM(CASE WHEN bd.turnover > 0 THEN bd.floatmv END), 0)       AS block_pct_1d,
    COUNT(bd.symbol)                                                         AS member_count,
    SUM(CASE WHEN bd.change_pct > 0 THEN 1 ELSE 0 END)                      AS rising_count,
    SUM(CASE
            WHEN RIGHT(bd.symbol,6) LIKE '9%'
                THEN (CASE WHEN bd.close >= ROUND(bd.close / (1 + bd.change_pct/100) * 1.3,  2) THEN 1 ELSE 0 END)
            WHEN RIGHT(bd.symbol,6) LIKE '688%' OR RIGHT(bd.symbol,6) LIKE '3%'
                THEN (CASE WHEN bd.close >= ROUND(bd.close / (1 + bd.change_pct/100) * 1.2,  2) THEN 1 ELSE 0 END)
            WHEN sp.name LIKE '%ST%'
                THEN (CASE WHEN bd.close >= ROUND(bd.close / (1 + bd.change_pct/100) * 1.05, 2) THEN 1 ELSE 0 END)
            ELSE      (CASE WHEN bd.close >= ROUND(bd.close / (1 + bd.change_pct/100) * 1.1,  2) THEN 1 ELSE 0 END)
        END)                                                                 AS limit_up_count
FROM raw_tdx_blocks_member bm
JOIN raw_tdx_blocks_info   bi ON bi.block_code = bm.block_code
JOIN stock_pool            sp ON sp.symbol = bm.stock_symbol
JOIN raw_basic_daily       bd ON bd.symbol = bm.stock_symbol AND bd.date = $target_date
GROUP BY bd.date, bm.block_code, bi.block_name, bi.block_type
"""

_SQL_BLOCK_DAILY_PCT_HISTORY = """
INSERT OR REPLACE INTO block_daily_pct
SELECT
    bd.date                                                                  AS trade_date,
    bm.block_code,
    bi.block_name,
    bi.block_type,
    SUM(CASE WHEN bd.turnover > 0 THEN bd.change_pct * bd.floatmv END)
        / NULLIF(SUM(CASE WHEN bd.turnover > 0 THEN bd.floatmv END), 0)       AS block_pct_1d,
    COUNT(bd.symbol)                                                         AS member_count,
    SUM(CASE WHEN bd.change_pct > 0 THEN 1 ELSE 0 END)                      AS rising_count,
    SUM(CASE
            WHEN RIGHT(bd.symbol,6) LIKE '9%'
                THEN (CASE WHEN bd.close >= ROUND(bd.close / (1 + bd.change_pct/100) * 1.3,  2) THEN 1 ELSE 0 END)
            WHEN RIGHT(bd.symbol,6) LIKE '688%' OR RIGHT(bd.symbol,6) LIKE '3%'
                THEN (CASE WHEN bd.close >= ROUND(bd.close / (1 + bd.change_pct/100) * 1.2,  2) THEN 1 ELSE 0 END)
            WHEN sp.name LIKE '%ST%'
                THEN (CASE WHEN bd.close >= ROUND(bd.close / (1 + bd.change_pct/100) * 1.05, 2) THEN 1 ELSE 0 END)
            ELSE      (CASE WHEN bd.close >= ROUND(bd.close / (1 + bd.change_pct/100) * 1.1,  2) THEN 1 ELSE 0 END)
        END)                                                                 AS limit_up_count
FROM raw_tdx_blocks_member bm
JOIN raw_tdx_blocks_info   bi ON bi.block_code = bm.block_code
JOIN stock_pool            sp ON sp.symbol = bm.stock_symbol
JOIN raw_basic_daily       bd ON bd.symbol = bm.stock_symbol
WHERE bd.date BETWEEN $start_date AND $end_date
GROUP BY bd.date, bm.block_code, bi.block_name, bi.block_type
"""

# ---------------------------------------------------------------------------
# Step 1b: rps_block_daily — pure window functions on block_daily_pct
# ---------------------------------------------------------------------------

_SQL_BLOCK_RPS_SINGLE = """
WITH block_returns AS (
    SELECT
        p.*,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 4  PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_5d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 9  PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_10d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 14 PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_15d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_20d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 49 PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_50d
    FROM block_daily_pct p
    WHERE p.member_count <= $max_member_count
),
ranked AS (
    SELECT
        r.*,
        PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_5d  NULLS FIRST) * 100 AS bkrps5,
        PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_10d NULLS FIRST) * 100 AS bkrps10,
        PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_15d NULLS FIRST) * 100 AS bkrps15,
        PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_20d NULLS FIRST) * 100 AS bkrps20,
        PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_50d NULLS FIRST) * 100 AS bkrps50
    FROM block_returns r
    WHERE r.trade_date = $target_date
)
INSERT OR REPLACE INTO rps_block_daily
SELECT
    r.trade_date, r.block_code, r.block_name, r.block_type,
    r.bkrps5, r.bkrps10, r.bkrps15, r.bkrps20, r.bkrps50,
    r.block_pct_1d, r.block_pct_5d, r.block_pct_10d, r.block_pct_20d, r.block_pct_50d,
    r.member_count, r.rising_count, r.limit_up_count
FROM ranked r
"""

_SQL_BLOCK_RPS_HISTORY = """
WITH block_returns AS (
    SELECT
        p.*,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 4  PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_5d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 9  PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_10d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 14 PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_15d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_20d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 49 PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_50d
    FROM block_daily_pct p
    WHERE p.member_count <= $max_member_count
),
ranked AS (
    SELECT
        r.*,
        PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_5d  NULLS FIRST) * 100 AS bkrps5,
        PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_10d NULLS FIRST) * 100 AS bkrps10,
        PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_15d NULLS FIRST) * 100 AS bkrps15,
        PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_20d NULLS FIRST) * 100 AS bkrps20,
        PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_50d NULLS FIRST) * 100 AS bkrps50
    FROM block_returns r
    WHERE r.trade_date BETWEEN $start_date AND $end_date
)
INSERT OR REPLACE INTO rps_block_daily
SELECT
    r.trade_date, r.block_code, r.block_name, r.block_type,
    r.bkrps5, r.bkrps10, r.bkrps15, r.bkrps20, r.bkrps50,
    r.block_pct_1d, r.block_pct_5d, r.block_pct_10d, r.block_pct_20d, r.block_pct_50d,
    r.member_count, r.rising_count, r.limit_up_count
FROM ranked r
"""

# ---------------------------------------------------------------------------
# Step 1c: block_breadth_daily — market breadth aggregated from per-stock flags
#   reads is_new_high_60 / is_new_low_60 / is_above_ma20 from rps_stock_daily,
#   so calc_stock_rps* must run first.
# ---------------------------------------------------------------------------

_BLOCK_BREADTH_CTE = """
WITH raw AS (
    SELECT
        s.trade_date,
        bm.block_code,
        bi.block_name,
        bi.block_type,
        COUNT(*)                                                       AS member_count,
        SUM(s.is_new_high_60)                                          AS new_high_count,
        SUM(s.is_new_low_60)                                           AS new_low_count,
        SUM(s.is_above_ma20)                                           AS above_ma20_count
    FROM rps_stock_daily        s
    JOIN raw_tdx_blocks_member  bm ON bm.stock_symbol = s.symbol
    JOIN raw_tdx_blocks_info    bi ON bi.block_code    = bm.block_code
    GROUP BY s.trade_date, bm.block_code, bi.block_name, bi.block_type
),
indexed AS (
    SELECT
        r.*,
        (r.new_high_count - r.new_low_count)                           AS nh_nl,
        CASE WHEN (r.new_high_count + r.new_low_count) > 0
             THEN r.new_high_count * 100.0 / (r.new_high_count + r.new_low_count)
             END                                                       AS high_low_index,
        CASE WHEN r.member_count > 0
             THEN r.above_ma20_count * 100.0 / r.member_count
             END                                                       AS breadth_ma20
    FROM raw r
),
smoothed AS (
    SELECT
        i.*,
        AVG(i.high_low_index) OVER (
            PARTITION BY i.block_code ORDER BY i.trade_date
            ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)                  AS high_low_index_ma10
    FROM indexed i
)
INSERT OR REPLACE INTO block_breadth_daily
SELECT
    trade_date, block_code, block_name, block_type,
    member_count, new_high_count, new_low_count,
    nh_nl, high_low_index, high_low_index_ma10,
    above_ma20_count, breadth_ma20
FROM smoothed
WHERE {date_filter}
"""

_SQL_BLOCK_BREADTH_SINGLE = _BLOCK_BREADTH_CTE.format(date_filter="trade_date = $target_date")
_SQL_BLOCK_BREADTH_HISTORY = _BLOCK_BREADTH_CTE.format(date_filter="trade_date BETWEEN $start_date AND $end_date")

# ---------------------------------------------------------------------------
# Step 2: rps_stock_daily — uses stock_pool cache instead of repeated filter JOIN
# ---------------------------------------------------------------------------

_STOCK_RPS_CTE = """
WITH returns AS (
    SELECT
        q.date,
        q.symbol,
        q.close                                                               AS close_qfq,
        q.high                                                                AS high_qfq,
        q.low                                                                 AS low_qfq,
        AVG(q.close) OVER (PARTITION BY q.symbol ORDER BY q.date
            ROWS BETWEEN 19  PRECEDING AND CURRENT ROW)                       AS ma20_qfq,
        MIN(q.low)  OVER (PARTITION BY q.symbol ORDER BY q.date
            ROWS BETWEEN 59  PRECEDING AND CURRENT ROW)                       AS llv60_qfq,
        (q.close / NULLIF(LAG(q.close, 5)   OVER w, 0) - 1) * 100           AS pct_5d,
        (q.close / NULLIF(LAG(q.close, 10)  OVER w, 0) - 1) * 100           AS pct_10d,
        (q.close / NULLIF(LAG(q.close, 20)  OVER w, 0) - 1) * 100           AS pct_20d,
        (q.close / NULLIF(LAG(q.close, 50)  OVER w, 0) - 1) * 100           AS pct_50d,
        (q.close / NULLIF(LAG(q.close, 120) OVER w, 0) - 1) * 100           AS pct_120d,
        (q.close / NULLIF(LAG(q.close, 250) OVER w, 0) - 1) * 100           AS pct_250d,
        MAX(q.high) OVER (PARTITION BY q.symbol ORDER BY q.date
            ROWS BETWEEN 59  PRECEDING AND CURRENT ROW)                      AS hhv60_qfq,
        MAX(q.high) OVER (PARTITION BY q.symbol ORDER BY q.date
            ROWS BETWEEN 149 PRECEDING AND CURRENT ROW)                      AS hhv150_qfq,
        MAX(q.high) OVER (PARTITION BY q.symbol ORDER BY q.date
            ROWS BETWEEN 249 PRECEDING AND CURRENT ROW)                      AS hhv250_qfq
    FROM v_stock_qfq q
    JOIN stock_pool sp ON sp.symbol = q.symbol
    WINDOW w AS (PARTITION BY q.symbol ORDER BY q.date)
),
ranked AS (
    SELECT
        r.*,
        PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_5d) * 100 AS rps5,
        CASE WHEN r.pct_10d  IS NOT NULL
             THEN PERCENT_RANK() OVER (
                      PARTITION BY r.date, (r.pct_10d  IS NOT NULL)
                      ORDER BY r.pct_10d ) * 100 END               AS rps10,
        CASE WHEN r.pct_20d  IS NOT NULL
             THEN PERCENT_RANK() OVER (
                      PARTITION BY r.date, (r.pct_20d  IS NOT NULL)
                      ORDER BY r.pct_20d ) * 100 END               AS rps20,
        CASE WHEN r.pct_50d  IS NOT NULL
             THEN PERCENT_RANK() OVER (
                      PARTITION BY r.date, (r.pct_50d  IS NOT NULL)
                      ORDER BY r.pct_50d ) * 100 END               AS rps50,
        CASE WHEN r.pct_120d IS NOT NULL
             THEN PERCENT_RANK() OVER (
                      PARTITION BY r.date, (r.pct_120d IS NOT NULL)
                      ORDER BY r.pct_120d) * 100 END               AS rps120,
        CASE WHEN r.pct_250d IS NOT NULL
             THEN PERCENT_RANK() OVER (
                      PARTITION BY r.date, (r.pct_250d IS NOT NULL)
                      ORDER BY r.pct_250d) * 100 END               AS rps250
    FROM returns r
    WHERE r.pct_5d IS NOT NULL
      AND {date_filter}
)
INSERT OR REPLACE INTO rps_stock_daily
SELECT
    r.date    AS trade_date,
    r.symbol,
    sp.name,
    r.rps5, r.rps10, r.rps20, r.rps50, r.rps120, r.rps250,
    r.pct_5d, r.pct_10d, r.pct_20d, r.pct_50d, r.pct_120d, r.pct_250d,
    r.close_qfq,
    r.hhv60_qfq, r.hhv150_qfq, r.hhv250_qfq,
    r.high_qfq / NULLIF(r.hhv150_qfq, 0) AS h_div_hhv150,
    r.high_qfq / NULLIF(r.hhv250_qfq, 0) AS h_div_hhv250,
    b.close   AS close_bfq,
    b.floatmv, b.totalmv, b.turnover, b.amount, b.change_pct,
    CASE WHEN r.high_qfq  >= r.hhv60_qfq  THEN 1 ELSE 0 END AS is_new_high_60,
    CASE WHEN r.low_qfq   <= r.llv60_qfq  THEN 1 ELSE 0 END AS is_new_low_60,
    CASE WHEN r.close_qfq >= r.ma20_qfq   THEN 1 ELSE 0 END AS is_above_ma20
FROM ranked r
JOIN stock_pool sp ON sp.symbol = r.symbol
LEFT JOIN v_stock_bfq b ON b.symbol = r.symbol AND b.date = r.date
"""

_SQL_STOCK_RPS_SINGLE = _STOCK_RPS_CTE.format(date_filter="r.date = $target_date")
_SQL_STOCK_RPS_HISTORY = _STOCK_RPS_CTE.format(date_filter="r.date BETWEEN $start_date AND $end_date")

_DEFAULT_MAX_MEMBER = 100


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calc_block_daily_pct(con: duckdb.DuckDBPyConnection, target_date: str) -> int:
    """Compute and cache block equal-weight daily returns for one date.

    Must be called before calc_block_rps for the same date.
    """
    con.execute(_SQL_BLOCK_DAILY_PCT_SINGLE, {"target_date": target_date})
    row = con.execute(
        "SELECT COUNT(*) FROM block_daily_pct WHERE trade_date = $1", [target_date]
    ).fetchone()
    return row[0] if row else 0


def calc_block_daily_pct_history(
    con: duckdb.DuckDBPyConnection, start_date: str, end_date: str
) -> int:
    """Bulk compute and cache block daily returns for a date range."""
    con.execute(_SQL_BLOCK_DAILY_PCT_HISTORY, {"start_date": start_date, "end_date": end_date})
    row = con.execute(
        "SELECT COUNT(*) FROM block_daily_pct WHERE trade_date BETWEEN $1 AND $2",
        [start_date, end_date],
    ).fetchone()
    return row[0] if row else 0


def calc_stock_rps(con: duckdb.DuckDBPyConnection, target_date: str) -> int:
    con.execute(_SQL_STOCK_RPS_SINGLE, {"target_date": target_date})
    row = con.execute(
        "SELECT COUNT(*) FROM rps_stock_daily WHERE trade_date = $1", [target_date]
    ).fetchone()
    return row[0] if row else 0


def calc_stock_rps_history(
    con: duckdb.DuckDBPyConnection, start_date: str, end_date: str
) -> int:
    con.execute(_SQL_STOCK_RPS_HISTORY, {"start_date": start_date, "end_date": end_date})
    row = con.execute(
        "SELECT COUNT(*) FROM rps_stock_daily WHERE trade_date BETWEEN $1 AND $2",
        [start_date, end_date],
    ).fetchone()
    return row[0] if row else 0


def calc_block_rps(
    con: duckdb.DuckDBPyConnection,
    target_date: str,
    max_member_count: int = _DEFAULT_MAX_MEMBER,
) -> int:
    con.execute(_SQL_BLOCK_RPS_SINGLE, {"target_date": target_date, "max_member_count": max_member_count})
    row = con.execute(
        "SELECT COUNT(*) FROM rps_block_daily WHERE trade_date = $1", [target_date]
    ).fetchone()
    return row[0] if row else 0


def calc_block_rps_history(
    con: duckdb.DuckDBPyConnection,
    start_date: str,
    end_date: str,
    max_member_count: int = _DEFAULT_MAX_MEMBER,
) -> int:
    con.execute(_SQL_BLOCK_RPS_HISTORY, {
        "start_date": start_date,
        "end_date": end_date,
        "max_member_count": max_member_count,
    })
    row = con.execute(
        "SELECT COUNT(*) FROM rps_block_daily WHERE trade_date BETWEEN $1 AND $2",
        [start_date, end_date],
    ).fetchone()
    return row[0] if row else 0


def calc_block_breadth(con: duckdb.DuckDBPyConnection, target_date: str) -> int:
    """Compute block market breadth for one date. Requires rps_stock_daily populated.

    high_low_index_ma10 needs the prior 9 trading days present in rps_stock_daily.
    """
    con.execute(_SQL_BLOCK_BREADTH_SINGLE, {"target_date": target_date})
    row = con.execute(
        "SELECT COUNT(*) FROM block_breadth_daily WHERE trade_date = $1", [target_date]
    ).fetchone()
    return row[0] if row else 0


def calc_block_breadth_history(
    con: duckdb.DuckDBPyConnection, start_date: str, end_date: str
) -> int:
    """Bulk compute block market breadth for a date range."""
    con.execute(_SQL_BLOCK_BREADTH_HISTORY, {"start_date": start_date, "end_date": end_date})
    row = con.execute(
        "SELECT COUNT(*) FROM block_breadth_daily WHERE trade_date BETWEEN $1 AND $2",
        [start_date, end_date],
    ).fetchone()
    return row[0] if row else 0