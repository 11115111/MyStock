"""申万行业分类同步（via akshare）。

申万2021三级行业分类，同步到 sw_industry（行业定义）和 sw_industry_member（个股映射）。

用法：
    from core.db import get_connection, init_tables
    from core.sw_sync import sync_sw_industry
    con = get_connection("your.duckdb"); init_tables(con)
    sync_sw_industry(con)

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


def sync_sw_industry(
    con: duckdb.DuckDBPyConnection,
    sleep: float = 0.5,
) -> dict[str, int]:
    """同步申万一二三级行业定义及个股映射。

    Returns:
        {"industries": N行业数, "members": N个股数}
    """
    import akshare as ak

    # ── 1. 获取所有申万指数列表（含一二三级）────────────────────────────
    spot = ak.sw_index_spot()
    # akshare 返回列：index_code, index_name, ... 以及可能的 level 列
    # 若无 level 列，根据 index_name 或约定的代码段区分
    # 申万一级共31个（代码 8010xx），二级约100+，三级约200+
    # akshare sw_index_spot 返回的 "级别" 列区分层级
    if "级别" in spot.columns:
        level_map = {"一级行业": 1, "二级行业": 2, "三级行业": 3}
        spot["level"] = spot["级别"].map(level_map)
    elif "level" in spot.columns:
        pass
    else:
        raise RuntimeError(f"无法识别申万行业层级，返回列：{list(spot.columns)}")

    spot = spot[spot["level"].notna()].copy()
    spot["level"] = spot["level"].astype(int)

    # 统一列名
    code_col = "指数代码" if "指数代码" in spot.columns else "index_code"
    name_col = "指数名称" if "指数名称" in spot.columns else "index_name"
    spot = spot.rename(columns={code_col: "code", name_col: "name"})

    # ── 2. 写入 sw_industry ───────────────────────────────────────────
    con.execute("DELETE FROM sw_industry")
    industry_df = spot[["code", "name", "level"]].drop_duplicates("code")
    con.register("_sw_ind", industry_df)
    con.execute("INSERT INTO sw_industry SELECT code, name, level FROM _sw_ind")
    con.unregister("_sw_ind")

    # ── 3. 按层级分组，逐行业拉成分股 ───────────────────────────────────
    l1 = spot[spot["level"] == 1][["code", "name"]].set_index("code")["name"].to_dict()
    l2 = spot[spot["level"] == 2][["code", "name"]].set_index("code")["name"].to_dict()
    l3 = spot[spot["level"] == 3][["code", "name"]].set_index("code")["name"].to_dict()

    # symbol → {l1/l2/l3 code+name}
    mapping: dict[str, dict] = {}

    for level, ind_dict in [(1, l1), (2, l2), (3, l3)]:
        for code, name in ind_dict.items():
            try:
                df = ak.sw_index_cons(index_id=code)
            except Exception as exc:
                print(f"[sw] {code} {name} 失败: {exc}")
                time.sleep(sleep)
                continue

            if df is None or df.empty:
                time.sleep(sleep)
                continue

            # akshare 返回列通常有 stock_code / 股票代码
            sc = "stock_code" if "stock_code" in df.columns else "股票代码"
            for raw_code in df[sc].astype(str):
                symbol = _to_prefixed(raw_code)
                if symbol not in mapping:
                    mapping[symbol] = {}
                mapping[symbol][f"l{level}_code"] = code
                mapping[symbol][f"l{level}_name"] = name

            time.sleep(sleep)

    # ── 4. 写入 sw_industry_member ────────────────────────────────────
    rows = [
        {
            "symbol": sym,
            "sw_l1_code": v.get("l1_code"),
            "sw_l1_name": v.get("l1_name"),
            "sw_l2_code": v.get("l2_code"),
            "sw_l2_name": v.get("l2_name"),
            "sw_l3_code": v.get("l3_code"),
            "sw_l3_name": v.get("l3_name"),
        }
        for sym, v in mapping.items()
    ]
    member_df = pd.DataFrame(rows)

    con.execute("DELETE FROM sw_industry_member")
    con.register("_sw_mem", member_df)
    con.execute("INSERT INTO sw_industry_member SELECT * FROM _sw_mem")
    con.unregister("_sw_mem")

    return {"industries": len(industry_df), "members": len(member_df)}
