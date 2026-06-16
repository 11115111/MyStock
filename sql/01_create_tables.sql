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

-- ===========================================================================
-- 情绪周期：涨跌停/炸板股池（来自东方财富，akshare 同步）
-- symbol 统一存交易所前缀格式（SH/SZ/BJ + 6位），与 raw_* 表一致
-- ===========================================================================

-- 涨停股池 ak.stock_zt_pool_em
CREATE TABLE IF NOT EXISTS zt_pool_daily (
    trade_date    DATE    NOT NULL,
    symbol        VARCHAR NOT NULL,
    name          VARCHAR,
    close         DOUBLE,             -- 最新价（收盘）
    pct_change    DOUBLE,             -- 涨跌幅 %
    amount        DOUBLE,             -- 成交额
    floatmv       DOUBLE,             -- 流通市值
    turnover      DOUBLE,             -- 换手率 %
    seal_amount   DOUBLE,             -- 封板资金
    first_seal_time VARCHAR,          -- 首次封板时间 HHMMSS
    last_seal_time  VARCHAR,          -- 最后封板时间 HHMMSS
    open_count    INTEGER,            -- 炸板次数
    zt_stat       VARCHAR,            -- 涨停统计 n/m（m天内n次涨停）
    consecutive   INTEGER,            -- 连板数
    industry      VARCHAR,            -- 所属行业
    PRIMARY KEY (trade_date, symbol)
);

-- 跌停股池 ak.stock_zt_pool_dtgc_em
CREATE TABLE IF NOT EXISTS dt_pool_daily (
    trade_date    DATE    NOT NULL,
    symbol        VARCHAR NOT NULL,
    name          VARCHAR,
    close         DOUBLE,
    pct_change    DOUBLE,
    amount        DOUBLE,
    floatmv       DOUBLE,
    turnover      DOUBLE,
    seal_amount   DOUBLE,             -- 板上成交额（封单资金）
    last_seal_time VARCHAR,           -- 最后封板时间
    consecutive_dt INTEGER,           -- 连续跌停天数
    open_count    INTEGER,            -- 开板次数
    industry      VARCHAR,
    PRIMARY KEY (trade_date, symbol)
);

-- 炸板股池 ak.stock_zt_pool_zbgc_em
CREATE TABLE IF NOT EXISTS zbgc_pool_daily (
    trade_date    DATE    NOT NULL,
    symbol        VARCHAR NOT NULL,
    name          VARCHAR,
    close         DOUBLE,
    pct_change    DOUBLE,
    amount        DOUBLE,
    floatmv       DOUBLE,
    turnover      DOUBLE,
    speed         DOUBLE,             -- 涨速
    first_seal_time VARCHAR,          -- 首次封板时间
    open_count    INTEGER,            -- 炸板次数
    zt_stat       VARCHAR,            -- 涨停统计
    amplitude     DOUBLE,             -- 振幅 %
    industry      VARCHAR,
    PRIMARY KEY (trade_date, symbol)
);

-- 情绪周期每日聚合：赚钱/亏钱效应指标
CREATE TABLE IF NOT EXISTS sentiment_daily (
    trade_date          DATE NOT NULL,
    zt_count            INTEGER,   -- 涨停家数（收盘封住）
    dt_count            INTEGER,   -- 跌停家数
    zbgc_count          INTEGER,   -- 炸板家数
    zt_seal_rate        DOUBLE,    -- 涨停封板率 = zt / (zt + zbgc)
    dt_seal_rate        DOUBLE,    -- 跌停封板率 = dt 中开板次数=0 占比
    max_consecutive     INTEGER,   -- 最高连板（高度板）
    lianban_count       INTEGER,   -- 连板家数（连板数>=2）
    prev_zt_return      DOUBLE,    -- 昨日涨停今日收益（昨收→今收均值 %）
    prev_lianban_return DOUBLE,    -- 昨日连板今日收益 %
    prev_zbgc_return    DOUBLE,    -- 昨日炸板今日收益 %
    break_count         INTEGER,   -- 当日断板家数（连板>=2 次日未涨停）
    break_risk_count    INTEGER,   -- 断板后3日内最低价较断板前收盘跌幅过半家数
    break_risk_ratio    DOUBLE,    -- break_risk_count / break_count
    PRIMARY KEY (trade_date)
);

-- 申万行业分类（一二三级）
CREATE TABLE IF NOT EXISTS sw_industry (
    code    VARCHAR PRIMARY KEY,
    name    VARCHAR NOT NULL,
    level   INTEGER NOT NULL  -- 1 / 2 / 3
);

-- 个股申万行业映射（扁平化，方便 JOIN）
CREATE TABLE IF NOT EXISTS sw_industry_member (
    symbol      VARCHAR PRIMARY KEY,
    sw_l1_code  VARCHAR,
    sw_l1_name  VARCHAR,
    sw_l2_code  VARCHAR,
    sw_l2_name  VARCHAR,
    sw_l3_code  VARCHAR,
    sw_l3_name  VARCHAR
);