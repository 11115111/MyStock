"""东财行业分类同步入口。

用法：
    python -m rps.cli.run_sw --db your.duckdb
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.db import get_connection, init_tables
from core.sw_sync import sync_industry


@click.command()
@click.option("--db", required=True, help="Path to DuckDB file")
@click.option("--sleep", "sleep_sec", default=0.3, show_default=True, help="Sleep between requests (seconds)")
def main(db: str, sleep_sec: float) -> None:
    con = get_connection(db)
    init_tables(con)
    click.echo("[industry] 同步东财行业分类...")
    n = sync_industry(con, sleep=sleep_sec)
    click.echo(f"  {n} 只个股写入 industry_member")
    con.close()
    click.echo("done.")


if __name__ == "__main__":
    main()
