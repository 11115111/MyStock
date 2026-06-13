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
def load_breadth_heatmap(_con_id: int, db_path: str, metric_col: str, n_days: int = 30) -> pd.DataFrame:
    """Last n_days × 二级行业 pivot table for one breadth metric."""
    _metric_map = {
        "MA20占比":   "ROUND(b.breadth_ma20, 1)",
        "NH-NL":      "b.nh_nl",
        "HL指数":     "ROUND(b.high_low_index, 1)",
        "HL指数MA10": "ROUND(b.high_low_index_ma10, 1)",
        "新高":       "b.new_high_count",
        "新低":       "b.new_low_count",
    }
    expr = _metric_map.get(metric_col, "ROUND(b.breadth_ma20, 1)")
    con = get_con(db_path)
    try:
        df = con.execute(f"""
            WITH latest AS (
                SELECT DISTINCT trade_date FROM block_breadth_daily
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
    dates = load_breadth_dates(con_id, db_path)
    if not dates:
        st.warning("block_breadth_daily 暂无数据，请先运行 `--init-history`")
        return

    c1, c2 = st.columns([2, 4])
    selected_date = c1.selectbox("日期", dates, index=0, key="bw_date")

    df = load_breadth_l2(con_id, db_path, selected_date)
    if df.empty:
        st.info(f"{selected_date} 暂无市场宽度数据")
        return

    # 全市场宽度概览（按二级行业汇总 ≈ 全市场）
    total_members = int(df["成员数"].sum())
    total_nh = int(df["新高"].sum())
    total_nl = int(df["新低"].sum())
    total_above = int(df["MA20上方家数"].sum())
    mkt_ma20 = total_above / total_members * 100 if total_members else 0
    mkt_hl = total_nh / (total_nh + total_nl) * 100 if (total_nh + total_nl) else 0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("二级行业数", len(df))
    m2.metric("全市场新高", total_nh)
    m3.metric("全市场新低", total_nl)
    m4.metric("全市场 NH-NL", total_nh - total_nl)
    m5.metric("全市场 MA20 宽度", f"{mkt_ma20:.1f}%")
    st.caption(f"全市场 High-Low Index ≈ {mkt_hl:.1f}（新高占新高+新低比）")

    st.divider()

    metric_choice = c2.selectbox(
        "排行指标", ["MA20占比", "NH-NL", "HL指数MA10", "新高", "新低"],
        index=0, key="bw_metric",
    )

    left, right = st.columns([3, 2])

    with left:
        st.subheader("二级行业宽度明细")
        st.dataframe(
            df.sort_values(metric_choice, ascending=False).reset_index(drop=True),
            use_container_width=True,
            height=560,
            column_config=_BREADTH_COL_CFG,
            hide_index=True,
        )
        csv = df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button("下载 CSV", data=csv,
                           file_name=f"breadth_l2_{selected_date}.csv", mime="text/csv")

    with right:
        st.subheader(f"按「{metric_choice}」排行")
        top_n = st.slider("显示行业数", 5, 40, 20, key="bw_topn")
        chart_df = (
            df[["行业", metric_choice]]
            .sort_values(metric_choice, ascending=False)
            .head(top_n)
            .set_index("行业")
        )
        st.bar_chart(chart_df, height=560)

    st.divider()

    # 热力表：日期 × 行业
    st.subheader("宽度热力表（近 30 日 × 二级行业）")
    hm_metric = st.selectbox(
        "指标", ["MA20占比", "NH-NL", "HL指数MA10", "HL指数", "新高", "新低"],
        index=0, key="bw_hm_metric",
    )
    pivot = load_breadth_heatmap(con_id, db_path, hm_metric)
    if pivot.empty:
        st.info("暂无数据")
    else:
        import streamlit.components.v1 as components
        import matplotlib.cm as mcm
        import matplotlib.colors as mcolors

        t = pivot.T
        t.columns = [d[5:] for d in t.columns]  # "2026-06-13" → "06-13"

        cmap_fn = mcm.get_cmap("coolwarm_r" if hm_metric == "新低" else "coolwarm")

        # 计算每列的颜色（axis=0 per-column 归一）
        colors = []  # shape: [n_rows][n_cols]
        vals = t.values.astype(float)
        for ci in range(vals.shape[1]):
            col = vals[:, ci]
            vmin, vmax = np.nanmin(col), np.nanmax(col)
            denom = vmax - vmin if vmax > vmin else 1.0
            normed = (col - vmin) / denom
            colors.append([mcolors.to_hex(cmap_fn(float(v))) for v in normed])
        # colors[ci][ri] → transpose to colors[ri][ci] for row-first layout
        colors_t = [[colors[ci][ri] for ci in range(len(colors))] for ri in range(len(t))]

        rows_json  = json.dumps(t.index.tolist())
        cols_json  = json.dumps(t.columns.tolist())
        vals_json  = json.dumps([[int(v) if not np.isnan(v) else None for v in row] for row in vals])
        colors_json = json.dumps(colors_t)
        n_rows = len(t)
        hdr_h = 70   # px reserved for 45° header
        row_h = 18   # px per data row
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
  let sortCol = cols.length - 1;  // default: newest date
  let sortAsc = true;

  function render() {{
    // header
    const hdr = document.getElementById('hdr-row');
    hdr.innerHTML = '';
    const th0 = document.createElement('th');
    th0.className = 'idx';
    th0.textContent = '';
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

    // sort row indices
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

    # 单行业时间序列
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
# Main UI
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("📈 A股情绪周期看板")

    db_path = _db_path_from_args()

    with st.sidebar:
        st.header("数据源")
        if db_path:
            st.success(f"已连接: `{db_path}`")
        else:
            db_path = st.text_input("DuckDB 文件路径", placeholder="/path/to/your.duckdb")
        if not db_path:
            st.info("请输入数据库路径或通过 `-- --db /path/to/db` 启动")
            return

    try:
        con = get_con(db_path)
        con_id = id(con)
    except Exception as e:
        st.error(f"无法连接数据库: {e}")
        return

    with st.sidebar:
        st.divider()
        st.header("模块")
        module = st.radio(
            "选择模块",
            ["📈 三线红榜单", "🌡️ 市场宽度"],
            index=0,
            label_visibility="collapsed",
        )

    if module == "📈 三线红榜单":
        render_sanxianhong(con_id, db_path)
    else:
        render_breadth(con_id, db_path)


if __name__ == "__main__":
    main()
