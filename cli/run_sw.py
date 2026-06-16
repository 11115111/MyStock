"""申万行业分类同步入口。

用法：
    python -m rps.cli.run_sw --db your.duckdb
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.db import get_connection, init_tables
from core.sw_sync import sync_sw_industry


@click.command()
@click.option("--db", required=True, help="Path to DuckDB file")
@click.option("--sleep", "sleep_sec", default=0.5, show_default=True, help="Sleep between requests (seconds)")
def main(db: str, sleep_sec: float) -> None:
    con = get_connection(db)
    init_tables(con)
    click.echo("[sw] 同步申万行业分类...")
    r = sync_sw_industry(con, sleep=sleep_sec)
    click.echo(f"  行业数={r['industries']} 个股映射={r['members']}")
    con.close()
    click.echo("done.")


if __name__ == "__main__":
    main()
