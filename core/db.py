import duckdb
from pathlib import Path

_SQL_CREATE = (Path(__file__).parent.parent / "sql" / "01_create_tables.sql").read_text(encoding="utf-8")


def get_connection(db_path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path)
    # 关掉 DuckDB 查询进度条：捕获 stdout 时它按 \r 刷新会变成刷屏的多行输出
    try:
        con.execute("SET enable_progress_bar=false")
    except Exception:
        pass
    return con


def init_tables(con: duckdb.DuckDBPyConnection) -> None:
    # 旧版 market_trin_daily 以成交量为主口径（含 trin_amt 列），口径已改为成交额：
    # 派生表直接删除重建，需重跑 --init-history 或 --only-trin 回填
    old = con.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'market_trin_daily' AND column_name = 'trin_amt'"
    ).fetchone()
    if old:
        con.execute("DROP TABLE market_trin_daily")
    for stmt in _SQL_CREATE.split(";"):
        stmt = stmt.strip()
        if stmt:
            con.execute(stmt)


def refresh_stock_pool(con: duckdb.DuckDBPyConnection) -> int:
    """Rebuild eligible stock pool from raw_symbol_class (class='stock').

    raw_symbol_name INNER JOIN already excludes 三板/B股/完全退市.
    ST, BSE (8x) stocks are kept so RPS ranks them fairly.
    Sanxianhong applies its own ST/delisted filter at query time.
    Call after raw_symbol_name or raw_symbol_class is updated. Returns pool size.
    """
    con.execute("DELETE FROM stock_pool")
    con.execute("""
        INSERT INTO stock_pool (symbol, name)
        SELECT s.symbol, n.name
        FROM raw_symbol_class s
        JOIN raw_symbol_name n ON n.symbol = s.symbol
        WHERE s.class = 'stock'
    """)
    row = con.execute("SELECT COUNT(*) FROM stock_pool").fetchone()
    return row[0] if row else 0
