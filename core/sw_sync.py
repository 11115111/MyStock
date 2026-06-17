"""申万行业分类同步（巨潮资讯 via akshare）。

一次调用 ak.stock_industry_category_cninfo() 获取全A股申万一二三级行业映射，
写入 sw_industry_member 表。

用法：
    from core.db import get_connection, init_tables
    from core.sw_sync import sync_sw_industry
    con = get_connection("your.duckdb"); init_tables(con)
    sync_sw_industry(con)

依赖：pip install akshare
"""
from __future__ import annotations

import duckdb
import pandas as pd


def _to_prefixed(code: str) -> str:
    """6 位代码 → 交易所前缀格式，与 raw_* 表一致。"""
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
    return "sh" + code


def sync_sw_industry(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """同步申万一二三级行业映射，返回写入行数。"""
    import akshare as ak

    df = ak.stock_industry_category_cninfo()

    out = pd.DataFrame({
        "symbol":      df["股票代码"].astype(str).map(_to_prefixed),
        "sw_l1_name":  df["一级行业"].astype(str),
        "sw_l2_name":  df["二级行业"].astype(str),
        "sw_l3_name":  df["三级行业"].astype(str),
    })

    # 同时更新 sw_industry（唯一行业名+层级）
    rows = []
    for level, col in [(1, "sw_l1_name"), (2, "sw_l2_name"), (3, "sw_l3_name")]:
        for name in out[col].dropna().unique():
            rows.append({"name": name, "level": level})
    ind_df = pd.DataFrame(rows).drop_duplicates()

    con.execute("DELETE FROM sw_industry")
    con.register("_sw_ind", ind_df)
    con.execute("INSERT INTO sw_industry (name, level) SELECT name, level FROM _sw_ind")
    con.unregister("_sw_ind")

    con.execute("DELETE FROM sw_industry_member")
    con.register("_sw_mem", out)
    con.execute("""
        INSERT INTO sw_industry_member (symbol, sw_l1_name, sw_l2_name, sw_l3_name)
        SELECT symbol, sw_l1_name, sw_l2_name, sw_l3_name FROM _sw_mem
    """)
    con.unregister("_sw_mem")

    return {"industries": len(ind_df), "members": len(out)}
