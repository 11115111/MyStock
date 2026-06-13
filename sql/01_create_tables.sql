-- 合格股票池缓存：排除 ST/退市/北交所/B股，随股票名称/分类数据更新时刷新
CREATE TABLE IF NOT EXISTS stock_pool (
    symbol VARCHAR PRIMARY KEY,
    name   VARCHAR
);

-- 板块每日涨跌幅缓存：每日 raw_basic_daily 写入后刷新一次
-- block RPS 的多周期复合收益率仅基于此表做窗口函数，无需再扫大表
CREATE TABLE IF NOT EXISTS block_daily_pct (
    trade_date    DATE    NOT NULL,
    block_code    VARCHAR NOT NULL,
    block_name    VARCHAR,
    block_type    VARCHAR,
    block_pct_1d  DOUBLE,
    member_count  INTEGER,
    rising_count  INTEGER,
    limit_up_count INTEGER,
    PRIMARY KEY (trade_date, block_code)
);

CREATE TABLE IF NOT EXISTS rps_stock_daily (
    trade_date DATE NOT NULL,
    symbol VARCHAR NOT NULL,
    name VARCHAR,
    rps5 DOUBLE, rps10 DOUBLE, rps20 DOUBLE, rps50 DOUBLE, rps120 DOUBLE, rps250 DOUBLE,  -- 改为 DOUBLE
    pct_5d DOUBLE, pct_10d DOUBLE, pct_20d DOUBLE, pct_50d DOUBLE, pct_120d DOUBLE, pct_250d DOUBLE,
    close_qfq DOUBLE,
    hhv60_qfq DOUBLE, hhv150_qfq DOUBLE, hhv250_qfq DOUBLE,
    h_div_hhv150 DOUBLE, h_div_hhv250 DOUBLE,
    close_bfq DOUBLE, floatmv DOUBLE, totalmv DOUBLE, turnover DOUBLE, amount DOUBLE, change_pct DOUBLE,
    is_new_high_60 INTEGER, is_new_low_60 INTEGER, is_above_ma20 INTEGER,  -- 个股宽度标志位
    PRIMARY KEY (trade_date, symbol)
);

-- 板块市场宽度：新高新低（NH-NL / High-Low Index）+ MA20 站上占比
CREATE TABLE IF NOT EXISTS block_breadth_daily (
    trade_date DATE NOT NULL,
    block_code VARCHAR NOT NULL,
    block_name VARCHAR,
    block_type VARCHAR,
    member_count       INTEGER,   -- 当日纳入计算的成员数（在 rps_stock_daily 中）
    new_high_count     INTEGER,   -- 60 日新高家数
    new_low_count      INTEGER,   -- 60 日新低家数
    nh_nl              INTEGER,    -- 新高 - 新低
    high_low_index     DOUBLE,    -- NH / (NH + NL) * 100
    high_low_index_ma10 DOUBLE,   -- High-Low Index 的 10 日均值（平滑）
    above_ma20_count   INTEGER,   -- 站上 MA20 的家数
    breadth_ma20       DOUBLE,    -- 站上 MA20 占比 %
    PRIMARY KEY (trade_date, block_code)
);

CREATE TABLE IF NOT EXISTS rps_block_daily (
    trade_date DATE NOT NULL,
    block_code VARCHAR NOT NULL,
    block_name VARCHAR,
    block_type VARCHAR,
    bkrps5 DOUBLE, bkrps10 DOUBLE, bkrps15 DOUBLE, bkrps20 DOUBLE, bkrps50 DOUBLE,  -- 改为 DOUBLE
    block_pct_1d DOUBLE, block_pct_5d DOUBLE, block_pct_10d DOUBLE, block_pct_20d DOUBLE, block_pct_50d DOUBLE,
    member_count INTEGER, rising_count INTEGER, limit_up_count INTEGER,
    PRIMARY KEY (trade_date, block_code)
);

CREATE TABLE IF NOT EXISTS sanxianhong_daily (
    trade_date DATE NOT NULL,
    symbol VARCHAR NOT NULL,
    name VARCHAR,
    rps50 DOUBLE, rps120 DOUBLE, rps250 DOUBLE,  -- 改为 DOUBLE
    h_div_hhv150 DOUBLE,
    formula_version VARCHAR,
    join_date DATE, consecutive_days INTEGER, total_days_60d INTEGER,
    enter_pool_count_60d INTEGER, last_exit_date DATE,
    close_bfq DOUBLE, floatmv DOUBLE, change_pct DOUBLE, turnover DOUBLE,
    PRIMARY KEY (trade_date, symbol, formula_version)
);