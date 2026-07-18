import duckdb
from pathlib import Path

_SQL_CREATE = (Path(__file__).parent.parent / "sql" / "01_create_tables.sql").read_text(encoding="utf-8")


def get_connection(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db_path)


def init_tables(con: duckdb.DuckDBPyConnection) -> None:
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
