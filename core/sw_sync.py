"""东财行业分类同步（via akshare）。

遍历 ak.stock_board_industry_name_em() 的所有行业，
逐一调用 ak.stock_board_industry_cons_em() 获取成分股，
写入 industry_member 表（symbol → 东财行业名称）。

用法：
    from core.db import get_connection, init_tables
    from core.sw_sync import sync_industry
    con = get_connection("your.duckdb"); init_tables(con)
    sync_industry(con)

依赖：pip install akshare
"""
from __future__ import annotations

import time

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


def sync_industry(
    con: duckdb.DuckDBPyConnection,
    sleep: float = 0.3,
) -> int:
    """同步东财行业分类，返回写入个股数。"""
    import akshare as ak

    industries = ak.stock_board_industry_name_em()
    # 返回列通常含 板块名称 / rank / 涨跌幅 等，取行业名
    name_col = "板块名称" if "板块名称" in industries.columns else industries.columns[0]
    names = industries[name_col].tolist()

    rows: list[dict] = []
    for name in names:
        try:
            df = ak.stock_board_industry_cons_em(symbol=name)
        except Exception as exc:
            print(f"[industry] {name} 失败: {exc}")
            time.sleep(sleep)
            continue

        if df is None or df.empty:
            time.sleep(sleep)
            continue

        code_col = "代码" if "代码" in df.columns else df.columns[0]
        for code in df[code_col].astype(str):
            rows.append({"symbol": _to_prefixed(code), "industry_name": name})

        time.sleep(sleep)

    if not rows:
        return 0

    out = pd.DataFrame(rows).drop_duplicates("symbol")
    con.execute("DELETE FROM industry_member")
    con.register("_ind_mem", out)
    con.execute("INSERT INTO industry_member SELECT symbol, industry_name FROM _ind_mem")
    con.unregister("_ind_mem")
    return len(out)
