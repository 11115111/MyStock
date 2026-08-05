"""情绪周期：涨跌停/炸板股池同步（东方财富 via akshare）。

东方财富的 zt/dt/zbgc 股池只保留近期数据（一般 ~60 天），需尽早抓取存档。
symbol 统一转成交易所前缀格式（SH/SZ/BJ + 6 位），与 raw_* 表一致以便 JOIN。

依赖：pip install akshare
用法：
    from core.db import get_connection, init_tables
    from core.sentiment_sync import sync_pools, sync_pools_range
    con = get_connection("your.duckdb"); init_tables(con)
    sync_pools(con, "20260613")                       # 单日
    sync_pools_range(con, "20260101", "20260613")     # 区间（交易日历过滤）
"""
from __future__ import annotations

import time

import duckdb


def _to_prefixed(code: str) -> str:
    """6 位代码 → 交易所前缀格式，与 raw_* 表一致。

    6/5/9(科创+沪B) 688 → SH；0/2/3 → SZ；4/8 三板/北交 → BJ。
    北交所代码以 4/8 开头（如 830xxx/87xxxx/430xxx），统一 BJ。
    """
    code = str(code).zfill(6)
    head = code[0]
    if head in ("6", "5"):
        return "sh" + code
    if code.startswith("688") or code.startswith("900"):
        return "sh" + code
    if head in ("0", "2", "3"):
        return "sz" + code
    if head in ("4", "8", "9"):
        return "bj" + code
    return "sh" + code  # 兜底


def _num(series):
    """转数值，'-'/空 → NULL。"""
    import pandas as pd
    return pd.to_numeric(series, errors="coerce")


def _sync_zt(con: duckdb.DuckDBPyConnection, date: str) -> int:
    import akshare as ak
    df = ak.stock_zt_pool_em(date=date)
    if df is None or df.empty:
        return 0
    out = df.rename(columns={
        "代码": "code", "名称": "name", "最新价": "close", "涨跌幅": "pct_change",
        "成交额": "amount", "流通市值": "floatmv", "换手率": "turnover",
        "封板资金": "seal_amount", "首次封板时间": "first_seal_time",
        "最后封板时间": "last_seal_time", "炸板次数": "open_count",
        "涨停统计": "zt_stat", "连板数": "consecutive", "所属行业": "industry",
    })
    out["trade_date"] = _to_date(date)
    out["symbol"] = out["code"].map(_to_prefixed)
    for c in ("close", "pct_change", "amount", "floatmv", "turnover", "seal_amount"):
        out[c] = _num(out[c])
    for c in ("open_count", "consecutive"):
        out[c] = _num(out[c]).astype("Int64")
    for c in ("first_seal_time", "last_seal_time", "zt_stat"):
        out[c] = out[c].astype(str)
    cols = ["trade_date", "symbol", "name", "close", "pct_change", "amount",
            "floatmv", "turnover", "seal_amount", "first_seal_time",
            "last_seal_time", "open_count", "zt_stat", "consecutive", "industry"]
    return _insert(con, "zt_pool_daily", out[cols], date)


def _sync_dt(con: duckdb.DuckDBPyConnection, date: str) -> int:
    import akshare as ak
    df = ak.stock_zt_pool_dtgc_em(date=date)
    if df is None or df.empty:
        return 0
    out = df.rename(columns={
        "代码": "code", "名称": "name", "最新价": "close", "涨跌幅": "pct_change",
        "成交额": "amount", "流通市值": "floatmv", "换手率": "turnover",
        "板上成交额": "seal_amount", "最后封板时间": "last_seal_time",
        "连续跌停": "consecutive_dt", "开板次数": "open_count", "所属行业": "industry",
    })
    out["trade_date"] = _to_date(date)
    out["symbol"] = out["code"].map(_to_prefixed)
    for c in ("close", "pct_change", "amount", "floatmv", "turnover", "seal_amount"):
        out[c] = _num(out[c])
    for c in ("consecutive_dt", "open_count"):
        out[c] = _num(out[c]).astype("Int64")
    out["last_seal_time"] = out["last_seal_time"].astype(str)
    cols = ["trade_date", "symbol", "name", "close", "pct_change", "amount",
            "floatmv", "turnover", "seal_amount", "last_seal_time",
            "consecutive_dt", "open_count", "industry"]
    return _insert(con, "dt_pool_daily", out[cols], date)


def _sync_zbgc(con: duckdb.DuckDBPyConnection, date: str) -> int:
    import akshare as ak
    df = ak.stock_zt_pool_zbgc_em(date=date)
    if df is None or df.empty:
        return 0
    out = df.rename(columns={
        "代码": "code", "名称": "name", "最新价": "close", "涨跌幅": "pct_change",
        "成交额": "amount", "流通市值": "floatmv", "换手率": "turnover",
        "涨速": "speed", "首次封板时间": "first_seal_time", "炸板次数": "open_count",
        "涨停统计": "zt_stat", "振幅": "amplitude", "所属行业": "industry",
    })
    out["trade_date"] = _to_date(date)
    out["symbol"] = out["code"].map(_to_prefixed)
    for c in ("close", "pct_change", "amount", "floatmv", "turnover", "speed", "amplitude"):
        out[c] = _num(out[c])
    out["open_count"] = _num(out["open_count"]).astype("Int64")
    for c in ("first_seal_time", "zt_stat"):
        out[c] = out[c].astype(str)
    cols = ["trade_date", "symbol", "name", "close", "pct_change", "amount",
            "floatmv", "turnover", "speed", "first_seal_time", "open_count",
            "zt_stat", "amplitude", "industry"]
    return _insert(con, "zbgc_pool_daily", out[cols], date)


def _to_date(date: str) -> str:
    """'20260613' → '2026-06-13'."""
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}"


def _insert(con: duckdb.DuckDBPyConnection, table: str, df, date: str) -> int:
    """幂等写入：先删当日再插入（避免 PK 冲突）。"""
    iso = _to_date(date)
    con.execute(f"DELETE FROM {table} WHERE trade_date = '{iso}'")
    con.register("_tmp_pool", df)
    con.execute(f"INSERT INTO {table} SELECT * FROM _tmp_pool")
    con.unregister("_tmp_pool")
    return len(df)


def sync_pools(con: duckdb.DuckDBPyConnection, date: str) -> dict[str, int]:
    """同步单日三张股池，返回各表写入行数。date 格式 'YYYYMMDD'。"""
    return {
        "zt":   _sync_zt(con, date),
        "dt":   _sync_dt(con, date),
        "zbgc": _sync_zbgc(con, date),
    }


def sync_pools_range(
    con: duckdb.DuckDBPyConnection,
    start: str,
    end: str,
    sleep: float = 0.5,
) -> dict[str, int]:
    """按交易日历遍历区间同步。start/end 格式 'YYYYMMDD'。"""
    import akshare as ak
    cal = ak.tool_trade_date_hist_sina()["trade_date"]
    s, e = _to_date(start), _to_date(end)
    days = [d for d in cal if s <= str(d) <= e]
    total = {"zt": 0, "dt": 0, "zbgc": 0}
    for d in days:
        ymd = str(d).replace("-", "")
        try:
            r = sync_pools(con, ymd)
            for k in total:
                total[k] += r[k]
            print(f"[{ymd}] zt={r['zt']} dt={r['dt']} zbgc={r['zbgc']}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{ymd}] failed: {exc}")
        time.sleep(sleep)
    return total
