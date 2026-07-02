"""活跃度门槛与在榜计算：成交额分布的 Pareto50% / 右侧拐点。

门槛靠直方图形状检出（拐点），纯 SQL 做不到，故用 Python 逐日算门槛落库，
再用一条 gap-and-islands SQL 算在榜 + 连续天数（与三线红同构）。

- active_threshold_daily：每日两个门槛（pareto_amt / knee_amt）
- active_pool_daily：每日在榜个股（zone=pareto/knee）+ 连续天数 + 进榜日

calc_active_history(start, end)：逐日算门槛 → 一条 SQL 扫全区间算在榜
calc_active(date) = 区间计算的单日子集
"""
from __future__ import annotations

import duckdb
import numpy as np

_HIST_BINS = 40


def compute_thresholds(amounts_yi: np.ndarray) -> tuple[float, float | None, float, int]:
    """从当日全市场成交额（亿）算 (pareto_amt, knee_amt|None, total_amt, count)。

    与 UI 的对数分布检测逻辑保持一致。
    """
    vals = amounts_yi[amounts_yi > 0]
    n = int(vals.size)
    if n == 0:
        return (0.0, None, 0.0, 0)

    total = float(vals.sum())

    # Pareto50%：成交额从大到小累加到全市场一半的门槛
    desc = np.sort(vals)[::-1]
    cum = np.cumsum(desc)
    k = int(np.searchsorted(cum, total * 0.5) + 1)
    pareto = float(desc[min(k, n) - 1])

    # 右侧拐点（肘部）：对数直方图主峰→右端下降段，离弦最远处
    knee: float | None = None
    if n >= 20:
        logv = np.log10(vals)
        lo, hi = logv.min(), logv.max()
        if hi > lo:
            edges = np.linspace(lo, hi, _HIST_BINS + 1)
            counts, _ = np.histogram(logv, bins=edges)
            centers = (edges[:-1] + edges[1:]) / 2
            sm = np.convolve(counts.astype(float), np.ones(3) / 3, mode="same")
            peak = int(np.argmax(sm))
            rx, ry = centers[peak:], sm[peak:]
            if len(rx) >= 3 and ry[0] > ry[-1]:
                xn = (rx - rx[0]) / (rx[-1] - rx[0] + 1e-12)
                yn = (ry - ry.min()) / (ry.max() - ry.min() + 1e-12)
                chord = yn[0] + (yn[-1] - yn[0]) * xn
                ki = int(np.argmax(chord - yn))
                if 0 < ki < len(rx) - 1:
                    knee = float(10 ** rx[ki])

    return (pareto, knee, total, n)


def _refresh_thresholds(con: duckdb.DuckDBPyConnection, start: str, end: str) -> int:
    """逐个交易日算门槛，写入 active_threshold_daily。返回处理天数。"""
    dates = [str(r[0]) for r in con.execute(
        """SELECT DISTINCT k.date
           FROM raw_kline_daily k
           WHERE k.date BETWEEN $1 AND $2
           ORDER BY k.date""", [start, end]).fetchall()]

    for d in dates:
        rows = con.execute("""
            SELECT k.amount / 1e8
            FROM raw_kline_daily k
            JOIN stock_pool sp ON sp.symbol = k.symbol
            WHERE k.date = $1 AND k.amount > 0
        """, [d]).fetchall()
        vals = np.array([r[0] for r in rows], dtype=float)
        pareto, knee, total, cnt = compute_thresholds(vals)
        con.execute("DELETE FROM active_threshold_daily WHERE trade_date = $1", [d])
        con.execute(
            """INSERT INTO active_threshold_daily
               (trade_date, pareto_amt, knee_amt, total_amt, stock_count)
               VALUES ($1, $2, $3, $4, $5)""",
            [d, pareto, knee, total, cnt],
        )
    return len(dates)


_POOL_SQL = """
INSERT OR REPLACE INTO active_pool_daily
WITH td AS (
    SELECT trade_date, ROW_NUMBER() OVER (ORDER BY trade_date) AS idx
    FROM (SELECT DISTINCT trade_date FROM active_threshold_daily)
),
mem AS (
    SELECT k.date AS trade_date, k.symbol, 'pareto' AS zone, k.amount / 1e8 AS amt
    FROM raw_kline_daily k
    JOIN stock_pool sp ON sp.symbol = k.symbol
    JOIN active_threshold_daily t ON t.trade_date = k.date
    WHERE t.pareto_amt IS NOT NULL AND k.amount / 1e8 >= t.pareto_amt
    UNION ALL
    SELECT k.date, k.symbol, 'knee', k.amount / 1e8
    FROM raw_kline_daily k
    JOIN stock_pool sp ON sp.symbol = k.symbol
    JOIN active_threshold_daily t ON t.trade_date = k.date
    WHERE t.knee_amt IS NOT NULL AND k.amount / 1e8 >= t.knee_amt
),
joined AS (
    SELECT m.*, td.idx FROM mem m JOIN td ON td.trade_date = m.trade_date
),
grp AS (
    SELECT *,
           idx - ROW_NUMBER() OVER (PARTITION BY symbol, zone ORDER BY idx) AS run_id
    FROM joined
),
runs AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY symbol, zone, run_id ORDER BY idx) AS consecutive_days,
           MIN(trade_date) OVER (PARTITION BY symbol, zone, run_id)          AS join_date
    FROM grp
)
SELECT
    r.trade_date, r.symbol, r.zone,
    sp.name,
    ROUND(r.amt, 2)          AS amount_yi,
    rr.rps50, rr.rps120, rr.rps250,
    rr.change_pct, rr.close_bfq, rr.floatmv,
    r.consecutive_days,
    r.join_date
FROM runs r
JOIN stock_pool sp ON sp.symbol = r.symbol
LEFT JOIN rps_stock_daily rr
       ON rr.symbol = r.symbol AND rr.trade_date = r.trade_date
WHERE r.trade_date BETWEEN $start AND $end
"""


def calc_active_history(con: duckdb.DuckDBPyConnection, start: str, end: str) -> int:
    """区间计算：先逐日算门槛，再一条 SQL 算在榜 + 连续天数。返回在榜行数。"""
    _refresh_thresholds(con, start, end)
    con.execute(_POOL_SQL, {"start": start, "end": end})
    row = con.execute(
        "SELECT COUNT(*) FROM active_pool_daily WHERE trade_date BETWEEN $s AND $e",
        {"s": start, "e": end},
    ).fetchone()
    return row[0] if row else 0


def calc_active(con: duckdb.DuckDBPyConnection, target_date: str) -> int:
    """单日计算 = 区间子集。门槛只算当日，连续天数窗口扫全表。"""
    _refresh_thresholds(con, target_date, target_date)
    con.execute(_POOL_SQL, {"start": target_date, "end": target_date})
    row = con.execute(
        "SELECT COUNT(*) FROM active_pool_daily WHERE trade_date = $1", [target_date]
    ).fetchone()
    return row[0] if row else 0
