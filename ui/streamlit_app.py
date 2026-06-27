"""A股情绪周期看板 — Streamlit app.

模块：
    📈 三线红榜单   — sanxianhong_daily 榜单 / 新上榜 / 退榜
    🌡️ 市场宽度     — block_breadth_daily 按通达信二级行业的 NH-NL / High-Low Index / MA20 宽度

Run from repo root:
    streamlit run rps/ui/streamlit_app.py -- --db /path/to/your.duckdb
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import json
import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="A股情绪周期看板",
    page_icon="📈",
    layout="wide",
)

# 通达信二级行业过滤口径（与 load_industry 一致）
_L2_FILTER = "i.block_type = 'tdx_research' AND i.block_level = 2"

# ---------------------------------------------------------------------------
# DB connection — cached per db path
# ---------------------------------------------------------------------------

@st.cache_resource
def get_con(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db_path, read_only=True)


def _db_path_from_args() -> str | None:
    """Read --db argument from streamlit's extra args (after --)."""
    args = sys.argv
    try:
        idx = args.index("--db")
        return args[idx + 1]
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Data loaders — 三线红
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_dates(_con_id: int, db_path: str) -> list[str]:
    con = get_con(db_path)
    rows = con.execute(
        "SELECT DISTINCT trade_date FROM sanxianhong_daily ORDER BY trade_date DESC LIMIT 120"
    ).fetchall()
    return [str(r[0]) for r in rows]


@st.cache_data(ttl=300)
def load_blocks(_con_id: int, db_path: str, trade_date: str, version: str) -> list[str]:
    """Return sorted list of blocks that contain stocks on this date."""
    con = get_con(db_path)
    try:
        rows = con.execute("""
            SELECT DISTINCT bi.block_name
            FROM sanxianhong_daily s
            JOIN raw_tdx_blocks_member bm ON bm.stock_symbol = s.symbol
            JOIN raw_tdx_blocks_info   bi ON bi.block_code   = bm.block_code
            WHERE s.trade_date = $1 AND s.formula_version = $2
            ORDER BY bi.block_name
        """, [trade_date, version]).fetchall()
        return ["全部"] + [r[0] for r in rows]
    except Exception:
        return ["全部"]


@st.cache_data(ttl=300)
def load_sanxianhong(
    _con_id: int,
    db_path: str,
    trade_date: str,
    version: str,
    block_filter: str | None,
) -> pd.DataFrame:
    con = get_con(db_path)

    block_join = ""
    block_where = ""
    if block_filter and block_filter != "全部":
        block_join = """
            JOIN raw_tdx_blocks_member bm ON bm.stock_symbol = s.symbol
            JOIN raw_tdx_blocks_info   bi ON bi.block_code   = bm.block_code
        """
        block_where = f"AND bi.block_name = '{block_filter.replace(chr(39), chr(39)+chr(39))}'"

    sql = f"""
        SELECT
            s.symbol                                    AS 代码,
            s.name                                      AS 名称,
            s.rps50                                     AS RPS50,
            s.rps120                                    AS RPS120,
            s.rps250                                    AS RPS250,
            ROUND(s.h_div_hhv150, 3)                    AS 近高比,
            s.consecutive_days                          AS 连续天数,
            s.total_days_60d                            AS "60日在榜",
            s.enter_pool_count_60d                      AS 上榜次数,
            s.join_date                                 AS 本轮入榜,
            s.last_exit_date                            AS 上次离场,
            ROUND(s.close_bfq, 2)                       AS 现价,
            ROUND(s.change_pct, 2)                      AS 涨跌幅,
            ROUND(s.turnover, 2)                        AS 换手率,
            ROUND(s.floatmv / 1e8, 1)                   AS 流通市值亿
        FROM sanxianhong_daily s
        {block_join}
        WHERE s.trade_date = $1
          AND s.formula_version = $2
          {block_where}
        ORDER BY s.consecutive_days DESC, s.rps50 DESC
    """
    try:
        df = con.execute(sql, [trade_date, version]).df()
    except Exception as e:
        st.error(f"查询失败: {e}")
        df = pd.DataFrame()

    if block_filter and block_filter != "全部" and not df.empty:
        df = df.drop_duplicates(subset=["代码"])

    return df


@st.cache_data(ttl=300)
def load_industry(_con_id: int, db_path: str) -> pd.DataFrame:
    """Return symbol → 所属行业 mapping (行业二级)."""
    con = get_con(db_path)
    try:
        return con.execute(f"""
            SELECT bm.stock_symbol AS symbol,
                   STRING_AGG(i.block_name, ' / ' ORDER BY i.block_name) AS 所属行业
            FROM raw_tdx_blocks_member bm
            JOIN raw_tdx_blocks_info   i ON i.block_code = bm.block_code
            WHERE {_L2_FILTER}
            GROUP BY bm.stock_symbol
        """).df()
    except Exception:
        return pd.DataFrame(columns=["symbol", "所属行业"])


def _attach_industry(df: pd.DataFrame, industry_df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.merge(industry_df, left_on="代码", right_on="symbol", how="left").drop(columns=["symbol"])
    cols = df.columns.tolist()
    cols.remove("所属行业")
    cols.insert(cols.index("名称") + 1, "所属行业")
    return df[cols]


@st.cache_data(ttl=300)
def load_new_entries(_con_id: int, db_path: str, trade_date: str, version: str) -> pd.DataFrame:
    """Stocks entering the list today (consecutive_days = 1)."""
    con = get_con(db_path)
    try:
        df = con.execute("""
            SELECT
                s.symbol                        AS 代码,
                s.name                          AS 名称,
                s.rps50                         AS RPS50,
                s.rps120                        AS RPS120,
                s.rps250                        AS RPS250,
                ROUND(s.h_div_hhv150, 3)        AS 近高比,
                s.last_exit_date                AS 上次离场,
                ROUND(s.close_bfq, 2)           AS 现价,
                ROUND(s.change_pct, 2)          AS 涨跌幅,
                ROUND(s.floatmv / 1e8, 1)       AS 流通市值亿
            FROM sanxianhong_daily s
            WHERE s.trade_date = $1
              AND s.formula_version = $2
              AND s.consecutive_days = 1
            ORDER BY s.rps50 DESC
        """, [trade_date, version]).df()
    except Exception:
        df = pd.DataFrame()
    return df


@st.cache_data(ttl=300)
def load_exits(_con_id: int, db_path: str, trade_date: str, version: str) -> pd.DataFrame:
    """Stocks that were on the list the previous trading day but not today."""
    con = get_con(db_path)
    try:
        # Previous trading date that has sanxianhong data
        row = con.execute("""
            SELECT MAX(trade_date) FROM sanxianhong_daily
            WHERE trade_date < $1 AND formula_version = $2
        """, [trade_date, version]).fetchone()
        prev_date = row[0] if row and row[0] else None
        if not prev_date:
            return pd.DataFrame()

        df = con.execute("""
            SELECT
                p.symbol                        AS 代码,
                p.name                          AS 名称,
                p.rps50                         AS RPS50,
                p.rps120                        AS RPS120,
                p.rps250                        AS RPS250,
                ROUND(p.h_div_hhv150, 3)        AS 近高比,
                p.consecutive_days              AS 离场前连续天数,
                ROUND(p.close_bfq, 2)           AS 现价,
                ROUND(p.change_pct, 2)          AS 涨跌幅,
                ROUND(p.floatmv / 1e8, 1)       AS 流通市值亿
            FROM sanxianhong_daily p
            WHERE p.trade_date = $1
              AND p.formula_version = $2
              AND p.symbol NOT IN (
                  SELECT symbol FROM sanxianhong_daily
                  WHERE trade_date = $3 AND formula_version = $2
              )
            ORDER BY p.consecutive_days DESC
        """, [str(prev_date), version, trade_date]).df()
    except Exception:
        df = pd.DataFrame()
    return df


# ---------------------------------------------------------------------------
# Data loaders — 市场宽度（通达信二级行业）
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_breadth_dates(_con_id: int, db_path: str) -> list[str]:
    con = get_con(db_path)
    try:
        rows = con.execute(
            "SELECT DISTINCT trade_date FROM block_breadth_daily ORDER BY trade_date DESC LIMIT 250"
        ).fetchall()
        return [str(r[0]) for r in rows]
    except Exception:
        return []


@st.cache_data(ttl=300)
def load_breadth_l2(_con_id: int, db_path: str, trade_date: str) -> pd.DataFrame:
    """每个二级行业当日的宽度指标。"""
    con = get_con(db_path)
    try:
        return con.execute(f"""
            SELECT
                b.block_name                    AS 行业,
                b.member_count                  AS 成员数,
                b.new_high_count                AS 新高,
                b.new_low_count                 AS 新低,
                b.nh_nl                         AS "NH-NL",
                ROUND(b.high_low_index, 1)      AS "HL指数",
                ROUND(b.high_low_index_ma10, 1) AS "HL指数MA10",
                ROUND(b.breadth_ma20, 1)        AS "MA20占比",
                b.above_ma20_count              AS "MA20上方家数"
            FROM block_breadth_daily b
            JOIN raw_tdx_blocks_info i ON i.block_code = b.block_code
            WHERE b.trade_date = $1 AND {_L2_FILTER}
            ORDER BY b.breadth_ma20 DESC
        """, [trade_date]).df()
    except Exception as e:
        st.error(f"查询失败: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_breadth_series(_con_id: int, db_path: str, block_name: str) -> pd.DataFrame:
    """单个二级行业的宽度时间序列。"""
    con = get_con(db_path)
    try:
        return con.execute(f"""
            SELECT
                b.trade_date                    AS 日期,
                b.nh_nl                          AS "NH-NL",
                ROUND(b.high_low_index, 1)      AS "HL指数",
                ROUND(b.high_low_index_ma10, 1) AS "HL指数MA10",
                ROUND(b.breadth_ma20, 1)        AS "MA20占比"
            FROM block_breadth_daily b
            JOIN raw_tdx_blocks_info i ON i.block_code = b.block_code
            WHERE i.block_name = $1 AND {_L2_FILTER}
            ORDER BY b.trade_date
        """, [block_name]).df()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_breadth_heatmap(_con_id: int, db_path: str, metric_col: str, end_date: str | None = None, n_days: int = 30) -> pd.DataFrame:
    """Last n_days up to end_date × 二级行业 pivot table for one breadth metric."""
    _metric_map = {
        "MA20占比":   "ROUND(b.breadth_ma20, 1)",
        "NH-NL":      "b.nh_nl",
        "HL指数":     "ROUND(b.high_low_index, 1)",
        "HL指数MA10": "ROUND(b.high_low_index_ma10, 1)",
        "新高":       "b.new_high_count",
        "新低":       "b.new_low_count",
    }
    expr = _metric_map.get(metric_col, "ROUND(b.breadth_ma20, 1)")
    date_filter = f"WHERE trade_date <= '{end_date}'" if end_date else ""
    con = get_con(db_path)
    try:
        df = con.execute(f"""
            WITH latest AS (
                SELECT DISTINCT trade_date FROM block_breadth_daily
                {date_filter}
                ORDER BY trade_date DESC LIMIT {n_days}
            )
            SELECT
                b.trade_date::VARCHAR   AS 日期,
                i.block_name            AS 行业,
                {expr}                  AS val
            FROM block_breadth_daily b
            JOIN raw_tdx_blocks_info i ON i.block_code = b.block_code
            JOIN latest l              ON l.trade_date  = b.trade_date
            WHERE {_L2_FILTER}
        """).df()
        if df.empty:
            return pd.DataFrame()
        pivot = df.pivot(index="日期", columns="行业", values="val")
        pivot = pivot.sort_index(ascending=True)
        return pivot
    except Exception as e:
        st.error(f"pivot 失败: {e}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Data loaders — 情绪周期
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_sentiment_series(_con_id: int, db_path: str, days: int = 120) -> pd.DataFrame:
    con = get_con(db_path)
    try:
        return con.execute(f"""
            SELECT trade_date AS 日期,
                   zt_count, dt_count, zbgc_count,
                   ROUND(zt_seal_rate * 100, 1)  AS zt_seal_pct,
                   ROUND(dt_seal_rate * 100, 1)  AS dt_seal_pct,
                   max_consecutive, lianban_count,
                   ROUND(prev_zt_return, 2)       AS prev_zt_ret,
                   ROUND(prev_lianban_return, 2)  AS prev_lb_ret,
                   ROUND(prev_zbgc_return, 2)     AS prev_zb_ret,
                   break_count, break_risk_count,
                   ROUND(break_risk_ratio * 100, 1) AS break_risk_pct
            FROM sentiment_daily
            ORDER BY trade_date DESC LIMIT {days}
        """).df().iloc[::-1].reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_pool_detail(_con_id: int, db_path: str, trade_date: str, pool: str) -> pd.DataFrame:
    con = get_con(db_path)
    table = {"涨停": "zt_pool_daily", "跌停": "dt_pool_daily", "炸板": "zbgc_pool_daily"}[pool]
    try:
        if pool == "涨停":
            return con.execute("""
                SELECT symbol AS 代码, name AS 名称, close AS 现价,
                       ROUND(pct_change,2) AS 涨跌幅,
                       consecutive AS 连板数,
                       open_count AS 炸板次数,
                       first_seal_time AS 首封时间,
                       last_seal_time AS 尾封时间,
                       ROUND(seal_amount/1e8,2) AS 封板资金亿,
                       zt_stat AS 涨停统计, industry AS 行业
                FROM zt_pool_daily WHERE trade_date=$1
                ORDER BY consecutive DESC, seal_amount DESC
            """, [trade_date]).df()
        if pool == "跌停":
            return con.execute("""
                SELECT symbol AS 代码, name AS 名称, close AS 现价,
                       ROUND(pct_change,2) AS 涨跌幅,
                       consecutive_dt AS 连续跌停,
                       open_count AS 开板次数,
                       last_seal_time AS 尾封时间,
                       ROUND(seal_amount/1e8,2) AS 封单资金亿,
                       industry AS 行业
                FROM dt_pool_daily WHERE trade_date=$1
                ORDER BY consecutive_dt DESC, seal_amount DESC
            """, [trade_date]).df()
        # 炸板
        return con.execute("""
            SELECT symbol AS 代码, name AS 名称, close AS 现价,
                   ROUND(pct_change,2) AS 涨跌幅,
                   open_count AS 炸板次数,
                   first_seal_time AS 首封时间,
                   ROUND(amplitude,1) AS 振幅,
                   zt_stat AS 涨停统计, industry AS 行业
            FROM zbgc_pool_daily WHERE trade_date=$1
            ORDER BY open_count DESC
        """, [trade_date]).df()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_sentiment_dates(_con_id: int, db_path: str) -> list[str]:
    con = get_con(db_path)
    try:
        rows = con.execute(
            "SELECT DISTINCT trade_date FROM sentiment_daily ORDER BY trade_date DESC LIMIT 250"
        ).fetchall()
        return [str(r[0]) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Module: 情绪周期
# ---------------------------------------------------------------------------

def render_sentiment(con_id: int, db_path: str) -> None:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    dates = load_sentiment_dates(con_id, db_path)
    if not dates:
        st.warning("sentiment_daily 暂无数据，请先同步股池并运行计算")
        return

    # 顶部日期选择（用于池详情）
    import datetime
    all_pool_dates = sorted({d for d in dates})
    tc1, tc2 = st.columns([2, 5])
    picked = tc1.date_input("查看日期（股池详情）", value=datetime.date.fromisoformat(dates[0]),
                             min_value=datetime.date.fromisoformat(dates[-1]),
                             max_value=datetime.date.fromisoformat(dates[0]), key="sent_date")
    selected_date = picked.isoformat()
    if selected_date not in {d for d in dates}:
        valid = [d for d in sorted(dates) if d <= selected_date]
        selected_date = valid[-1] if valid else dates[0]

    df = load_sentiment_series(con_id, db_path)
    if df.empty:
        st.info("暂无情绪数据")
        return

    df["日期"] = pd.to_datetime(df["日期"])

    # ── 统计概览（选中日当日）──────────────────────────────────────────
    row = df[df["日期"].dt.date.astype(str) == selected_date]
    if not row.empty:
        r = row.iloc[0]
        m1,m2,m3,m4,m5,m6,m7 = st.columns(7)
        m1.metric("涨停", int(r["zt_count"]) if pd.notna(r["zt_count"]) else "-")
        m2.metric("跌停", int(r["dt_count"]) if pd.notna(r["dt_count"]) else "-")
        m3.metric("炸板", int(r["zbgc_count"]) if pd.notna(r["zbgc_count"]) else "-")
        m4.metric("涨停封板率", f"{r['zt_seal_pct']:.1f}%" if pd.notna(r["zt_seal_pct"]) else "-")
        m5.metric("最高连板", int(r["max_consecutive"]) if pd.notna(r["max_consecutive"]) else "-")
        m6.metric("昨涨停今收益", f"{r['prev_zt_ret']:.2f}%" if pd.notna(r["prev_zt_ret"]) else "-")
        m7.metric("断板风险率", f"{r['break_risk_pct']:.1f}%" if pd.notna(r["break_risk_pct"]) else "-")

    st.divider()

    # ── 时序图 ─────────────────────────────────────────────────────────
    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True,
        subplot_titles=("涨跌停家数", "封板率 %", "T+1 收益 %", "断板风险 %"),
        vertical_spacing=0.07,
        row_heights=[0.3, 0.2, 0.25, 0.25],
    )
    x = df["日期"]

    # 1. 涨跌停家数
    fig.add_trace(go.Bar(x=x, y=df["zt_count"],  name="涨停", marker_color="#d62728"), row=1, col=1)
    fig.add_trace(go.Bar(x=x, y=-df["dt_count"], name="跌停", marker_color="#1f77b4"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["zbgc_count"], name="炸板",
                             mode="lines", line=dict(color="orange", width=1.5)), row=1, col=1)

    # 2. 封板率
    fig.add_trace(go.Scatter(x=x, y=df["zt_seal_pct"], name="涨停封板率",
                             mode="lines", line=dict(color="#d62728", width=1.5)), row=2, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["dt_seal_pct"], name="跌停封板率",
                             mode="lines", line=dict(color="#1f77b4", width=1.5, dash="dot")), row=2, col=1)

    # 3. T+1 收益
    fig.add_trace(go.Scatter(x=x, y=df["prev_zt_ret"], name="昨涨停今收益",
                             mode="lines", line=dict(color="#d62728", width=1.5)), row=3, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["prev_lb_ret"], name="昨连板今收益",
                             mode="lines", line=dict(color="purple", width=1.5)), row=3, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["prev_zb_ret"], name="昨炸板今收益",
                             mode="lines", line=dict(color="orange", width=1.5)), row=3, col=1)
    fig.add_hline(y=0, line_dash="dash", line_color="gray", row=3, col=1)

    # 4. 断板风险
    fig.add_trace(go.Scatter(x=x, y=df["break_risk_pct"], name="断板风险%",
                             mode="lines", fill="tozeroy",
                             line=dict(color="crimson", width=1.5)), row=4, col=1)

    fig.update_layout(height=700, showlegend=True,
                      legend=dict(orientation="h", y=-0.05),
                      margin=dict(l=40, r=20, t=40, b=40),
                      barmode="relative")
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ── 当日股池详情 ──────────────────────────────────────────────────
    st.subheader(f"股池详情 — {selected_date}")
    tab_zt, tab_dt, tab_zb = st.tabs(["📈 涨停池", "📉 跌停池", "💥 炸板池"])

    with tab_zt:
        d = load_pool_detail(con_id, db_path, selected_date, "涨停")
        if d.empty:
            st.info("当日无涨停数据（需先同步股池）")
        else:
            st.caption(f"共 {len(d)} 只")
            st.dataframe(d, use_container_width=True, height=500, hide_index=True)

    with tab_dt:
        d = load_pool_detail(con_id, db_path, selected_date, "跌停")
        if d.empty:
            st.info("当日无跌停数据")
        else:
            st.caption(f"共 {len(d)} 只")
            st.dataframe(d, use_container_width=True, height=500, hide_index=True)

    with tab_zb:
        d = load_pool_detail(con_id, db_path, selected_date, "炸板")
        if d.empty:
            st.info("当日无炸板数据")
        else:
            st.caption(f"共 {len(d)} 只")
            st.dataframe(d, use_container_width=True, height=500, hide_index=True)


# ---------------------------------------------------------------------------
# Shared column config
# ---------------------------------------------------------------------------

_COL_CFG = {
    "代码":         st.column_config.TextColumn("代码", width="small"),
    "名称":         st.column_config.TextColumn("名称", width="small"),
    "所属行业":     st.column_config.TextColumn("所属行业", width="medium"),
    "RPS50":        st.column_config.NumberColumn("RPS50",  format="%.2f"),
    "RPS120":       st.column_config.NumberColumn("RPS120", format="%.2f"),
    "RPS250":       st.column_config.NumberColumn("RPS250", format="%.2f"),
    "近高比":       st.column_config.NumberColumn("近高比", format="%.3f"),
    "连续天数":     st.column_config.NumberColumn("连续天数", format="%d"),
    "离场前连续天数": st.column_config.NumberColumn("离场前连续天数", format="%d"),
    "60日在榜":     st.column_config.NumberColumn("60日在榜", format="%d"),
    "上榜次数":     st.column_config.NumberColumn("上榜次数", format="%d"),
    "本轮入榜":     st.column_config.DateColumn("本轮入榜"),
    "上次离场":     st.column_config.DateColumn("上次离场"),
    "现价":         st.column_config.NumberColumn("现价",   format="%.2f"),
    "涨跌幅":       st.column_config.NumberColumn("涨跌幅", format="%.2f%%"),
    "换手率":       st.column_config.NumberColumn("换手率", format="%.2f%%"),
    "流通市值亿":   st.column_config.NumberColumn("流通市值(亿)", format="%.1f"),
}

_BREADTH_COL_CFG = {
    "行业":        st.column_config.TextColumn("行业", width="medium"),
    "成员数":      st.column_config.NumberColumn("成员数", format="%d"),
    "新高":        st.column_config.NumberColumn("新高", format="%d"),
    "新低":        st.column_config.NumberColumn("新低", format="%d"),
    "NH-NL":       st.column_config.NumberColumn("NH-NL", format="%d"),
    "HL指数":      st.column_config.NumberColumn("HL指数", format="%.1f"),
    "HL指数MA10":  st.column_config.NumberColumn("HL指数MA10", format="%.1f"),
    "MA20占比":    st.column_config.NumberColumn("MA20占比", format="%.1f%%"),
    "MA20上方家数": st.column_config.NumberColumn("MA20上方家数", format="%d"),
}


# ---------------------------------------------------------------------------
# Module: 三线红榜单
# ---------------------------------------------------------------------------

def render_sanxianhong(con_id: int, db_path: str) -> None:
    dates = load_dates(con_id, db_path)
    if not dates:
        st.warning("sanxianhong_daily 暂无数据，请先运行 `--init-history`")
        return

    # 顶部筛选条
    c1, c2, c3, c4, c5 = st.columns([2, 1.5, 2, 2, 1])
    selected_date = c1.selectbox("日期", dates, index=0, key="szh_date")
    version = c2.selectbox("版本", ["strict", "loose"], index=0, key="szh_ver",
                           help="strict: rps50/120/250 全部达标 + h_div_hhv150\nloose: 任一RPS ≥ 阈值 + h_div_hhv250")
    blocks = load_blocks(con_id, db_path, selected_date, version)
    selected_block = c3.selectbox("板块筛选", blocks, index=0, key="szh_block")
    sort_col = c4.selectbox(
        "排序列",
        ["连续天数", "60日在榜", "上榜次数", "RPS50", "RPS120", "RPS250", "近高比", "流通市值亿", "涨跌幅", "换手率"],
        index=0, key="szh_sort",
    )
    sort_asc = c5.checkbox("升序", value=False, key="szh_asc")

    # Load data
    df = load_sanxianhong(con_id, db_path, selected_date, version, selected_block)
    df_new = load_new_entries(con_id, db_path, selected_date, version)
    df_exit = load_exits(con_id, db_path, selected_date, version)
    industry_df = load_industry(con_id, db_path)
    df = _attach_industry(df, industry_df)
    df_new = _attach_industry(df_new, industry_df)
    df_exit = _attach_industry(df_exit, industry_df)

    if df.empty:
        st.info(f"{selected_date} 暂无三线红数据")
        return

    if sort_col in df.columns:
        df = df.sort_values(sort_col, ascending=sort_asc)

    # Summary metrics
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("在榜股票数", len(df))
    col2.metric("平均连续天数", f"{df['连续天数'].mean():.1f}")
    col3.metric("平均RPS50", f"{df['RPS50'].mean():.1f}")
    col4.metric("平均流通市值(亿)", f"{df['流通市值亿'].mean():.1f}" if df['流通市值亿'].notna().any() else "N/A")
    col5.metric("今日新上榜", len(df_new))
    col6.metric("今日退榜", len(df_exit))

    st.divider()

    st.dataframe(
        df.reset_index(drop=True),
        use_container_width=True,
        height=600,
        column_config=_COL_CFG,
        hide_index=True,
    )

    csv = df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(
        label="下载 CSV",
        data=csv,
        file_name=f"sanxianhong_{selected_date}.csv",
        mime="text/csv",
    )

    st.divider()

    with st.expander(f"🟢 今日新上榜 ({len(df_new)} 只)", expanded=True):
        if df_new.empty:
            st.info("今日无新上榜")
        else:
            st.dataframe(df_new.reset_index(drop=True), use_container_width=True,
                         column_config=_COL_CFG, hide_index=True)

    with st.expander(f"🔴 今日退榜 ({len(df_exit)} 只)", expanded=True):
        if df_exit.empty:
            st.info("今日无退榜")
        else:
            st.dataframe(df_exit.reset_index(drop=True), use_container_width=True,
                         column_config=_COL_CFG, hide_index=True)


# ---------------------------------------------------------------------------
# Module: 市场宽度（通达信二级行业）
# ---------------------------------------------------------------------------

def render_breadth(con_id: int, db_path: str) -> None:
    import datetime
    import streamlit.components.v1 as components
    import matplotlib as mpl
    import matplotlib.colors as mcolors

    dates = load_breadth_dates(con_id, db_path)
    if not dates:
        st.warning("block_breadth_daily 暂无数据，请先运行 `--init-history`")
        return

    date_objs = sorted({datetime.date.fromisoformat(d) for d in dates})
    min_d, max_d = date_objs[0], date_objs[-1]
    date_set = {d.isoformat() for d in date_objs}

    # ── 顶部控件行 ─────────────────────────────────────────────────────
    tc1, tc2 = st.columns([2, 3])
    picked = tc1.date_input(
        "日期", value=max_d, min_value=min_d, max_value=max_d, key="bw_date",
    )
    # 若选中日期不在数据中，向前找最近有效日期
    selected_date = picked.isoformat()
    if selected_date not in date_set:
        valid = [d for d in sorted(date_set) if d <= selected_date]
        selected_date = valid[-1] if valid else sorted(date_set)[-1]

    hm_metric = tc2.selectbox(
        "热力表指标", ["MA20占比", "NH-NL", "HL指数MA10", "HL指数", "新高", "新低"],
        index=0, key="bw_hm_metric",
    )

    # ── 全市场统计概览 ─────────────────────────────────────────────────
    df = load_breadth_l2(con_id, db_path, selected_date)
    if df.empty:
        st.info(f"{selected_date} 暂无市场宽度数据")
        return

    total_members = int(df["成员数"].sum())
    total_nh      = int(df["新高"].sum())
    total_nl      = int(df["新低"].sum())
    total_above   = int(df["MA20上方家数"].sum())
    mkt_ma20 = total_above / total_members * 100 if total_members else 0
    mkt_hl   = total_nh / (total_nh + total_nl) * 100 if (total_nh + total_nl) else 0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("二级行业数", len(df))
    m2.metric("全市场新高", total_nh)
    m3.metric("全市场新低", total_nl)
    m4.metric("全市场 NH-NL", total_nh - total_nl)
    m5.metric("全市场 MA20 宽度", f"{mkt_ma20:.1f}%")
    st.caption(f"全市场 High-Low Index ≈ {mkt_hl:.1f}")

    st.divider()

    # ── 热力表（统计下方）─────────────────────────────────────────────
    pivot = load_breadth_heatmap(con_id, db_path, hm_metric, end_date=selected_date)
    if not pivot.empty:
        t = pivot.T
        t.columns = [d[5:] for d in t.columns]
        cmap_fn = mpl.colormaps["coolwarm_r" if hm_metric == "新低" else "coolwarm"]
        vals = t.values.astype(float)
        colors = []
        for ci in range(vals.shape[1]):
            col = vals[:, ci]
            vmin, vmax = np.nanmin(col), np.nanmax(col)
            denom = vmax - vmin if vmax > vmin else 1.0
            normed = (col - vmin) / denom
            colors.append([mcolors.to_hex(cmap_fn(float(v))) for v in normed])
        colors_t = [[colors[ci][ri] for ci in range(len(colors))] for ri in range(len(t))]

        rows_json   = json.dumps(t.index.tolist())
        cols_json   = json.dumps(t.columns.tolist())
        vals_json   = json.dumps([[int(v) if not np.isnan(v) else None for v in row] for row in vals])
        colors_json = json.dumps(colors_t)
        n_rows = len(t)
        hdr_h, row_h = 70, 18
        total_h = hdr_h + n_rows * row_h + 20

        html = f"""
<style>
  #hm-wrap {{font-family:monospace;font-size:10px;width:100%;overflow:hidden}}
  #hm-table {{border-collapse:collapse;width:100%}}
  #hm-table td {{padding:0 3px;line-height:{row_h}px;height:{row_h}px;text-align:center;white-space:nowrap;cursor:default}}
  #hm-table th.idx {{padding:0 4px;text-align:left;vertical-align:bottom;white-space:nowrap;height:{hdr_h}px}}
  #hm-table th.col-hdr {{height:{hdr_h}px;padding:0;vertical-align:bottom;text-align:left;cursor:pointer;white-space:nowrap}}
  #hm-table th.col-hdr div {{transform:rotate(-45deg);transform-origin:left bottom;width:1.5em;margin-left:8px;padding-bottom:2px}}
  #hm-table th.col-hdr.asc::after  {{content:" ▲";font-size:8px}}
  #hm-table th.col-hdr.desc::after {{content:" ▼";font-size:8px}}
  #hm-table tr:hover td {{outline:1px solid #888}}
</style>
<div id="hm-wrap"><table id="hm-table"><thead><tr id="hdr-row"></tr></thead><tbody id="body"></tbody></table></div>
<script>
(function(){{
  const rows   = {rows_json};
  const cols   = {cols_json};
  const vals   = {vals_json};
  const colors = {colors_json};
  let sortCol = cols.length - 1;
  let sortAsc = true;
  function contrast(hex) {{
    const r=parseInt(hex.slice(1,3),16), g=parseInt(hex.slice(3,5),16), b=parseInt(hex.slice(5,7),16);
    return (0.299*r + 0.587*g + 0.114*b) / 255 > 0.55 ? '#000' : '#fff';
  }}
  function render() {{
    const hdr = document.getElementById('hdr-row');
    hdr.innerHTML = '';
    const th0 = document.createElement('th');
    th0.className = 'idx';
    hdr.appendChild(th0);
    cols.forEach((c, ci) => {{
      const th = document.createElement('th');
      th.className = 'col-hdr' + (ci===sortCol ? (sortAsc?' asc':' desc') : '');
      th.innerHTML = '<div>' + c + '</div>';
      th.onclick = () => {{
        if (sortCol === ci) {{ sortAsc = !sortAsc; }}
        else {{ sortCol = ci; sortAsc = true; }}
        render();
      }};
      hdr.appendChild(th);
    }});
    const order = rows.map((_, i) => i).sort((a, b) => {{
      const av = vals[a][sortCol], bv = vals[b][sortCol];
      if (av===null && bv===null) return 0;
      if (av===null) return 1;
      if (bv===null) return -1;
      return sortAsc ? av - bv : bv - av;
    }});
    const tbody = document.getElementById('body');
    tbody.innerHTML = '';
    order.forEach(ri => {{
      const tr = document.createElement('tr');
      const td0 = document.createElement('td');
      td0.style.textAlign = 'left';
      td0.textContent = rows[ri];
      tr.appendChild(td0);
      vals[ri].forEach((v, ci) => {{
        const td = document.createElement('td');
        td.style.background = colors[ri][ci];
        td.style.color = contrast(colors[ri][ci]);
        td.textContent = v === null ? '' : v;
        tr.appendChild(td);
      }});
      tbody.appendChild(tr);
    }});
  }}
  render();
}})();
</script>
"""
        components.html(html, height=total_h, scrolling=False)

    st.divider()

    # ── 行业明细表 ─────────────────────────────────────────────────────
    metric_choice = st.selectbox(
        "明细排序", ["MA20占比", "NH-NL", "HL指数MA10", "新高", "新低"],
        index=0, key="bw_metric",
    )
    st.dataframe(
        df.sort_values(metric_choice, ascending=False).reset_index(drop=True),
        use_container_width=True,
        height=480,
        column_config=_BREADTH_COL_CFG,
        hide_index=True,
    )
    csv = df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("下载 CSV", data=csv,
                       file_name=f"breadth_l2_{selected_date}.csv", mime="text/csv")

    st.divider()

    # ── 单行业时间序列 ─────────────────────────────────────────────────
    st.subheader("行业宽度走势")
    industries = df.sort_values("MA20占比", ascending=False)["行业"].tolist()
    sel = st.selectbox("选择二级行业", industries, index=0, key="bw_series")
    series = load_breadth_series(con_id, db_path, sel)
    if series.empty:
        st.info("无走势数据")
        return

    series = series.set_index("日期")
    cc1, cc2 = st.columns(2)
    with cc1:
        st.caption("HL指数 / HL指数MA10 / MA20占比")
        st.line_chart(series[["HL指数", "HL指数MA10", "MA20占比"]], height=300)
    with cc2:
        st.caption("NH-NL（新高 - 新低）")
        st.bar_chart(series[["NH-NL"]], height=300)


# ---------------------------------------------------------------------------
# 自选 RPS 筛选
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def load_screen_dates(_con_id: int, db_path: str) -> list[str]:
    con = get_con(db_path)
    rows = con.execute(
        "SELECT DISTINCT trade_date FROM rps_stock_daily ORDER BY trade_date DESC LIMIT 120"
    ).fetchall()
    return [str(r[0]) for r in rows]


@st.cache_data(ttl=60)
def load_screen(
    _con_id: int, db_path: str, trade_date: str,
    rps50_min: float, rps120_min: float, rps250_min: float,
    hhv_ratio_min: float, mode: str,
) -> pd.DataFrame:
    con = get_con(db_path)
    if mode == "strict":
        where = (
            f"r.rps50 >= {rps50_min} AND r.rps120 >= {rps120_min} "
            f"AND r.rps250 >= {rps250_min} AND r.h_div_hhv150 >= {hhv_ratio_min}"
        )
        hhv_col = "r.h_div_hhv150 AS 近高比150"
    else:
        where = (
            f"(r.rps50 >= {rps50_min} OR r.rps120 >= {rps120_min} OR r.rps250 >= {rps250_min}) "
            f"AND r.h_div_hhv250 >= {hhv_ratio_min}"
        )
        hhv_col = "r.h_div_hhv250 AS 近高比250"
    sql = f"""
        SELECT r.symbol, n.name AS 名称,
               ROUND(r.rps50,2)  AS rps50,
               ROUND(r.rps120,2) AS rps120,
               ROUND(r.rps250,2) AS rps250,
               {hhv_col},
               ROUND(r.close_bfq,2) AS 现价,
               ROUND(r.change_pct,2) AS 涨跌幅,
               ROUND(r.floatmv/1e8,1) AS 流通市值亿,
               ROUND(r.turnover,2) AS 换手率,
               (s.symbol IS NOT NULL) AS 在榜
        FROM rps_stock_daily r
        JOIN raw_symbol_name n ON n.symbol = r.symbol
        LEFT JOIN sanxianhong_daily s
               ON s.symbol = r.symbol AND s.trade_date = r.trade_date
              AND s.formula_version = $2
        WHERE r.trade_date = $1 AND {where}
          AND n.name NOT LIKE '%ST%' AND n.name NOT LIKE '%退%'
        ORDER BY 在榜 DESC, r.rps50 DESC
    """
    return con.execute(sql, [trade_date, mode]).df()


@st.cache_data(ttl=60)
def load_board_only(
    _con_id: int, db_path: str, trade_date: str,
    rps50_min: float, rps120_min: float, rps250_min: float,
    hhv_ratio_min: float, mode: str,
) -> pd.DataFrame:
    """榜单有、但不满足自选条件的股票。"""
    con = get_con(db_path)
    if mode == "strict":
        not_where = (
            f"NOT (r.rps50 >= {rps50_min} AND r.rps120 >= {rps120_min} "
            f"AND r.rps250 >= {rps250_min} AND r.h_div_hhv150 >= {hhv_ratio_min})"
        )
        hhv_col = "r.h_div_hhv150 AS 近高比150"
    else:
        not_where = (
            f"NOT ((r.rps50 >= {rps50_min} OR r.rps120 >= {rps120_min} OR r.rps250 >= {rps250_min}) "
            f"AND r.h_div_hhv250 >= {hhv_ratio_min})"
        )
        hhv_col = "r.h_div_hhv250 AS 近高比250"
    sql = f"""
        SELECT r.symbol, s.name AS 名称,
               ROUND(r.rps50,2)  AS rps50,
               ROUND(r.rps120,2) AS rps120,
               ROUND(r.rps250,2) AS rps250,
               {hhv_col},
               ROUND(r.close_bfq,2) AS 现价,
               ROUND(r.change_pct,2) AS 涨跌幅,
               ROUND(r.floatmv/1e8,1) AS 流通市值亿,
               ROUND(r.turnover,2) AS 换手率
        FROM sanxianhong_daily s
        JOIN rps_stock_daily r ON r.symbol = s.symbol AND r.trade_date = s.trade_date
        WHERE s.trade_date = $1 AND s.formula_version = $2
          AND {not_where}
        ORDER BY r.rps50 DESC
    """
    return con.execute(sql, [trade_date, mode]).df()


def render_screen(con_id: int, db_path: str) -> None:
    dates = load_screen_dates(con_id, db_path)
    if not dates:
        st.warning("rps_stock_daily 暂无数据，请先运行 `--init-history`")
        return

    # ── 控件行 ────────────────────────────────────────────────────────
    c1, c2 = st.columns([2, 1])
    selected_date = c1.selectbox("日期", dates, index=0, key="sc_date")
    mode = c2.radio("模式", ["strict", "loose"], horizontal=True, key="sc_mode")

    st.divider()
    s1, s2, s3, s4, s5 = st.columns(5)
    rps50_min  = s1.number_input("RPS50 ≥",  min_value=0.0, max_value=99.0, value=90.0, step=0.1, format="%.1f", key="sc_rps50")
    rps120_min = s2.number_input("RPS120 ≥", min_value=0.0, max_value=99.0, value=93.0, step=0.1, format="%.1f", key="sc_rps120")
    rps250_min = s3.number_input("RPS250 ≥", min_value=0.0, max_value=99.0, value=95.0, step=0.1, format="%.1f", key="sc_rps250")
    hhv_min    = s4.number_input("近高比 ≥", min_value=0.0, max_value=1.0,  value=0.85, step=0.01, format="%.2f", key="sc_hhv")
    run        = s5.button("查询", type="primary", use_container_width=True, key="sc_run")

    # ── 结果分组显示 ──────────────────────────────────────────────────
    if run:
        df = load_screen(con_id, db_path, selected_date,
                         float(rps50_min), float(rps120_min), float(rps250_min),
                         float(hhv_min), mode)
        df_board_only = load_board_only(con_id, db_path, selected_date,
                                        float(rps50_min), float(rps120_min), float(rps250_min),
                                        float(hhv_min), mode)
        st.session_state["sc_result"] = df
        st.session_state["sc_board_only"] = df_board_only

    df = st.session_state.get("sc_result")
    df_board_only = st.session_state.get("sc_board_only", pd.DataFrame())
    if df is not None:
        cols = [c for c in df.columns if c != "在榜"]
        on_board  = df[df["在榜"] == True][cols].reset_index(drop=True)
        off_board = df[df["在榜"] == False][cols].reset_index(drop=True)

        t1, t2, t3 = st.tabs([
            f"自选 ∩ 在榜 ({len(on_board)})",
            f"自选独有 ({len(off_board)})",
            f"榜单独有 ({len(df_board_only)})",
        ])
        with t1:
            if on_board.empty:
                st.info("无")
            else:
                st.dataframe(on_board, use_container_width=True, hide_index=True)
        with t2:
            if off_board.empty:
                st.info("无")
            else:
                st.dataframe(off_board, use_container_width=True, hide_index=True)
        with t3:
            if df_board_only.empty:
                st.info("无")
            else:
                st.dataframe(df_board_only, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# 数据管理：tdx2db 初始化/更新 + 本项目数据初始化/刷新
# ---------------------------------------------------------------------------

def _run_command(cmd: list[str], cwd: str | None = None) -> None:
    """运行命令并把输出实时打到 UI。"""
    import subprocess

    st.code(" ".join(cmd), language="bash")
    placeholder = st.empty()
    lines: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except FileNotFoundError as e:
        st.error(f"无法启动: {e}")
        return

    for line in proc.stdout:  # type: ignore[union-attr]
        lines.append(line.rstrip("\n"))
        placeholder.code("\n".join(lines[-200:]))
    proc.wait()
    if proc.returncode == 0:
        st.success(f"完成 (exit {proc.returncode})")
    else:
        st.error(f"失败 (exit {proc.returncode})")


def _release_db_connections() -> None:
    """释放 streamlit 持有的只读连接，避免 DuckDB 写锁冲突。"""
    try:
        get_con.clear()
    except Exception:
        pass


def _pick_directory() -> str | None:
    """弹出系统原生目录选择框（本地运行时有效）。返回所选路径或 None。"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        path = filedialog.askdirectory(master=root)
        root.destroy()
        return path or None
    except Exception as e:  # 嵌入式 python 可能缺 tkinter
        st.warning(f"无法打开目录选择框（{e}），请手动粘贴路径。")
        return None


def render_data_mgmt(db_path: str) -> None:
    import os

    repo_root = str(Path(__file__).parent.parent)  # ui/ 的上一级即仓库根

    # DB 固定放 data 子目录，不在 UI 配置
    data_dir = os.path.join(repo_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    cur_db = db_path or os.path.join(data_dir, "tdx.db")
    tdx_exe = os.path.join(repo_root, "tdx2db.exe")
    dburi = f"duckdb://{cur_db}"

    if not os.path.exists(cur_db):
        st.info(
            "👋 检测到数据库尚未初始化。首次使用请按顺序操作：\n\n"
            "1️⃣ 选择通达信 vipdoc 目录 → 点「tdx2db 初始化」\n\n"
            "2️⃣ 完成后点「本项目初始化历史」\n\n"
            "之后日常只需依次点两个「日常更新 / 刷新」即可。"
        )

    st.caption(f"数据库：`{cur_db}`")
    st.caption("运行前会释放本应用对数据库的连接，避免与 tdx2db 写入冲突。")

    st.divider()
    st.markdown("#### 1. tdx2db 行情数据")

    # ── vipdoc 目录选择 ───────────────────────────────────────────────
    pc1, pc2 = st.columns([1, 3])
    if pc1.button("📂 选择 vipdoc 目录", use_container_width=True, key="dm_pick"):
        picked = _pick_directory()
        if picked:
            # 直接写 widget 的 state key（keyed widget 忽略 value=，需改 state）
            st.session_state["dm_vipdoc"] = picked
            st.rerun()
    vipdoc = pc2.text_input("通达信 vipdoc 目录（初始化用）",
                            placeholder=r"C:\new_tdx\vipdoc", key="dm_vipdoc")

    c1, c2 = st.columns(2)
    if c1.button("🆕 初始化（首次全量）", use_container_width=True, key="dm_tdx_init"):
        if not vipdoc:
            st.warning("请先选择 vipdoc 目录")
        else:
            _release_db_connections()
            _run_command([tdx_exe, "init", "--dburi", dburi, "--dayfiledir", vipdoc])
    if c2.button("🔄 日常更新", use_container_width=True, key="dm_tdx_cron"):
        _release_db_connections()
        _run_command([tdx_exe, "cron", "--dburi", dburi])

    st.divider()
    st.markdown("#### 2. 本项目数据（RPS / 三线红 / 市场宽度）")
    d1, d2 = st.columns(2)
    py = sys.executable
    if d1.button("🆕 初始化历史", use_container_width=True, key="dm_rps_init"):
        _release_db_connections()
        _run_command([py, "-m", "cli.run_daily", "--db", cur_db, "--init-history"],
                     cwd=repo_root)
    if d2.button("🔄 日常刷新", use_container_width=True, key="dm_rps_daily"):
        _release_db_connections()
        _run_command([py, "-m", "cli.run_daily", "--db", cur_db], cwd=repo_root)


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------

def main() -> None:
    import os

    db_path = _db_path_from_args()
    if not db_path:
        # 默认数据库：仓库 data/tdx.db，无需用户选择
        repo_root = str(Path(__file__).parent.parent)
        os.makedirs(os.path.join(repo_root, "data"), exist_ok=True)
        db_path = os.path.join(repo_root, "data", "tdx.db")

    with st.sidebar:
        st.header("数据源")
        if os.path.exists(db_path):
            st.success(f"已连接: `{db_path}`")
        else:
            st.warning(f"数据库未初始化\n\n`{db_path}`\n\n请到 ⚙️ 数据管理 初始化")

    # 数据库可能尚未初始化（首次使用），连接失败时仍允许进入数据管理模块
    con_id: int | None = None
    con_err: str | None = None
    try:
        con = get_con(db_path)
        con_id = id(con)
    except Exception as e:
        con_err = str(e)

    _MODULES = {
        "sanxianhong": ("📈", "三线红榜单"),
        "screen":      ("🔍", "自选筛选"),
        "breadth":     ("🌡️", "市场宽度"),
        # "sentiment":   ("🔥", "情绪周期"),  # 暂时隐藏
        "data_mgmt":   ("⚙️", "数据管理"),
    }
    db_exists = os.path.exists(db_path)
    if "module" not in st.session_state:
        # 首次启动若数据库尚未初始化，直接引导到数据管理
        st.session_state.module = "sanxianhong" if db_exists else "data_mgmt"

    with st.sidebar:
        st.divider()
        st.markdown("#### 模块导航")
        for key, (icon, label) in _MODULES.items():
            active = st.session_state.module == key
            btn_style = (
                "background:#1f77b4;color:#fff;border:none;border-radius:6px;"
                "padding:8px 12px;width:100%;text-align:left;font-size:15px;cursor:pointer;margin-bottom:4px;"
                if active else
                "background:transparent;color:inherit;border:1px solid #ccc;border-radius:6px;"
                "padding:8px 12px;width:100%;text-align:left;font-size:15px;cursor:pointer;margin-bottom:4px;"
            )
            if st.button(f"{icon} {label}", key=f"nav_{key}",
                         use_container_width=True,
                         type="primary" if active else "secondary"):
                st.session_state.module = key
                st.rerun()

    module = st.session_state.module
    icon, label = _MODULES[module]
    st.title(f"{icon} {label}")

    if module == "data_mgmt":
        render_data_mgmt(db_path)
        return

    if con_id is None:
        st.error(f"无法连接数据库: {con_err}")
        st.info("如果是首次使用，请先到 ⚙️ 数据管理 初始化数据。")
        return

    if module == "sanxianhong":
        render_sanxianhong(con_id, db_path)
    elif module == "screen":
        render_screen(con_id, db_path)
    elif module == "breadth":
        render_breadth(con_id, db_path)
    elif module == "sentiment":
        render_sentiment(con_id, db_path)


if __name__ == "__main__":
    main()
