"""情绪周期数据管道入口。

用法：
    # 同步单日股池（需要 akshare + 网络）
    python -m rps.cli.run_sentiment --db your.duckdb --sync --date 2026-06-13

    # 同步区间股池
    python -m rps.cli.run_sentiment --db your.duckdb --sync --start 2026-01-01 --end 2026-06-13

    # 计算情绪指标（股池已同步，不需要网络）
    python -m rps.cli.run_sentiment --db your.duckdb --calc

    # 同步 + 计算一步完成
    python -m rps.cli.run_sentiment --db your.duckdb --sync --calc --date 2026-06-13
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.db import get_connection, init_tables
from core.sentiment_sync import sync_pools, sync_pools_range
from core.sentiment_calc import calc_sentiment, calc_sentiment_history


@click.command()
@click.option("--db", required=True, help="Path to DuckDB file")
@click.option("--date", "target_date", default=None, help="Target date YYYY-MM-DD")
@click.option("--start", "start_date", default=None, help="Range start YYYY-MM-DD")
@click.option("--end",   "end_date",   default=None, help="Range end   YYYY-MM-DD")
@click.option("--sync",  "do_sync",  is_flag=True, help="Sync zt/dt/zbgc pools from Eastmoney (requires akshare + internet)")
@click.option("--calc",  "do_calc",  is_flag=True, help="Compute sentiment_daily aggregates")
@click.option("--sleep", "sleep_sec", default=0.5, show_default=True, help="Sleep between requests (seconds)")
def main(
    db: str,
    target_date: str | None,
    start_date: str | None,
    end_date: str | None,
    do_sync: bool,
    do_calc: bool,
    sleep_sec: float,
) -> None:
    if not do_sync and not do_calc:
        raise click.UsageError("Specify at least one of --sync / --calc")

    # 确定日期范围
    is_range = bool(start_date or end_date)
    if is_range and not (start_date and end_date):
        raise click.UsageError("--start and --end must be used together")
    if not is_range and not target_date:
        raise click.UsageError("Provide --date or --start/--end")

    con = get_connection(db)
    init_tables(con)

    if is_range:
        if do_sync:
            ymd_s = start_date.replace("-", "")
            ymd_e = end_date.replace("-", "")
            click.echo(f"[sync] {start_date} → {end_date}")
            r = sync_pools_range(con, ymd_s, ymd_e, sleep=sleep_sec)
            click.echo(f"  total zt={r['zt']} dt={r['dt']} zbgc={r['zbgc']}")

        if do_calc:
            click.echo(f"[calc] {start_date} → {end_date}")
            n = calc_sentiment_history(con, start_date, end_date)
            click.echo(f"  {n} rows into sentiment_daily")
    else:
        if do_sync:
            ymd = target_date.replace("-", "")
            click.echo(f"[sync] {target_date}")
            r = sync_pools(con, ymd)
            click.echo(f"  zt={r['zt']} dt={r['dt']} zbgc={r['zbgc']}")

        if do_calc:
            click.echo(f"[calc] {target_date}")
            n = calc_sentiment(con, target_date)
            click.echo(f"  {n} rows into sentiment_daily")

    con.close()
    click.echo("done.")


if __name__ == "__main__":
    main()
