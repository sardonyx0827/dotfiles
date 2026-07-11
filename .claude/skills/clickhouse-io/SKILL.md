---
name: clickhouse-io
description: ClickHouse database patterns, query optimization, analytics, and data engineering best practices for high-performance analytical workloads.
---

# ClickHouse Analytics Patterns

ClickHouse-specific patterns for high-performance analytics and data engineering.

## Overview

ClickHouse is a column-oriented database management system (DBMS) for online analytical processing (OLAP). It's optimized for fast analytical queries on large datasets.

**Key Features:**
- Column-oriented storage
- Data compression
- Parallel query execution
- Distributed queries
- Real-time analytics

## Table Design Patterns

### MergeTree Engine (Most Common)

```sql
CREATE TABLE products_analytics (
    date Date,
    product_id String,
    product_name String,
    sales UInt64,
    orders UInt32,
    unique_buyers UInt32,
    avg_order_size Float64,
    created_at DateTime
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (date, product_id)
SETTINGS index_granularity = 8192;
```

### ReplacingMergeTree (Deduplication)

```sql
-- For data that may have duplicates (e.g., from multiple sources)
CREATE TABLE user_events (
    event_id String,
    user_id String,
    event_type String,
    timestamp DateTime,
    properties String
) ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (user_id, event_id, timestamp)
PRIMARY KEY (user_id, event_id);
```

### AggregatingMergeTree (Pre-aggregation)

```sql
-- For maintaining aggregated metrics
CREATE TABLE product_stats_hourly (
    hour DateTime,
    product_id String,
    total_sales AggregateFunction(sum, UInt64),
    total_orders AggregateFunction(count, UInt32),
    unique_users AggregateFunction(uniq, String)
) ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(hour)
ORDER BY (hour, product_id);

-- Query aggregated data
SELECT
    hour,
    product_id,
    sumMerge(total_sales) AS sales,
    countMerge(total_orders) AS orders,
    uniqMerge(unique_users) AS users
FROM product_stats_hourly
WHERE hour >= toStartOfHour(now() - INTERVAL 24 HOUR)
GROUP BY hour, product_id
ORDER BY hour DESC;
```

## Query Optimization Patterns

### Efficient Filtering

```sql
-- ✅ GOOD: Use indexed columns first
SELECT *
FROM products_analytics
WHERE date >= '2025-01-01'
  AND product_id = 'product-123'
  AND sales > 1000
ORDER BY date DESC
LIMIT 100;

-- ❌ BAD: Filter on non-indexed columns first
SELECT *
FROM products_analytics
WHERE sales > 1000
  AND product_name LIKE '%wireless%'
  AND date >= '2025-01-01';
```

### Aggregations

```sql
-- ✅ GOOD: Use ClickHouse-specific aggregation functions
SELECT
    toStartOfDay(created_at) AS day,
    product_id,
    sum(sales) AS total_sales,
    count() AS total_orders,
    uniq(buyer_id) AS unique_buyers,
    avg(order_size) AS avg_size
FROM orders
WHERE created_at >= today() - INTERVAL 7 DAY
GROUP BY day, product_id
ORDER BY day DESC, total_sales DESC;

-- ✅ Use quantile for percentiles (more efficient than percentile)
SELECT
    quantile(0.50)(order_size) AS median,
    quantile(0.95)(order_size) AS p95,
    quantile(0.99)(order_size) AS p99
FROM orders
WHERE created_at >= now() - INTERVAL 1 HOUR;
```

### Window Functions

```sql
-- Calculate running totals
SELECT
    date,
    product_id,
    sales,
    sum(sales) OVER (
        PARTITION BY product_id
        ORDER BY date
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_sales
FROM products_analytics
WHERE date >= today() - INTERVAL 30 DAY
ORDER BY product_id, date;
```

## Data Insertion Patterns

### Bulk Insert (Recommended)

```typescript
import { ClickHouse } from 'clickhouse'

const clickhouse = new ClickHouse({
  url: process.env.CLICKHOUSE_URL,
  port: 8123,
  basicAuth: {
    username: process.env.CLICKHOUSE_USER,
    password: process.env.CLICKHOUSE_PASSWORD
  }
})

// ✅ Batch insert (efficient)
async function bulkInsertOrders(orders: Order[]) {
  const values = orders.map(order => `(
    '${order.id}',
    '${order.product_id}',
    '${order.user_id}',
    ${order.amount},
    '${order.timestamp.toISOString()}'
  )`).join(',')

  await clickhouse.query(`
    INSERT INTO orders (id, product_id, user_id, amount, timestamp)
    VALUES ${values}
  `).toPromise()
}

// ❌ Individual inserts (slow)
async function insertOrder(order: Order) {
  // Don't do this in a loop!
  await clickhouse.query(`
    INSERT INTO orders VALUES ('${order.id}', ...)
  `).toPromise()
}
```

### Streaming Insert

```typescript
// For continuous data ingestion
import { createWriteStream } from 'fs'
import { pipeline } from 'stream/promises'

async function streamInserts() {
  const stream = clickhouse.insert('orders').stream()

  for await (const batch of dataSource) {
    stream.write(batch)
  }

  await stream.end()
}
```

## Materialized Views

### Real-time Aggregations

```sql
-- Create materialized view for hourly stats
CREATE MATERIALIZED VIEW product_stats_hourly_mv
TO product_stats_hourly
AS SELECT
    toStartOfHour(timestamp) AS hour,
    product_id,
    sumState(amount) AS total_sales,
    countState() AS total_orders,
    uniqState(user_id) AS unique_users
FROM orders
GROUP BY hour, product_id;

-- Query the materialized view
SELECT
    hour,
    product_id,
    sumMerge(total_sales) AS sales,
    countMerge(total_orders) AS orders,
    uniqMerge(unique_users) AS users
FROM product_stats_hourly
WHERE hour >= now() - INTERVAL 24 HOUR
GROUP BY hour, product_id;
```

## Performance Monitoring

### Query Performance

```sql
-- Check slow queries
SELECT
    query_id,
    user,
    query,
    query_duration_ms,
    read_rows,
    read_bytes,
    memory_usage
FROM system.query_log
WHERE type = 'QueryFinish'
  AND query_duration_ms > 1000
  AND event_time >= now() - INTERVAL 1 HOUR
ORDER BY query_duration_ms DESC
LIMIT 10;
```

### Table Statistics

```sql
-- Check table sizes
SELECT
    database,
    table,
    formatReadableSize(sum(bytes)) AS size,
    sum(rows) AS rows,
    max(modification_time) AS latest_modification
FROM system.parts
WHERE active
GROUP BY database, table
ORDER BY sum(bytes) DESC;
```

## Common Analytics Queries

### Time Series Analysis

```sql
-- Daily active users
SELECT
    toDate(timestamp) AS date,
    uniq(user_id) AS daily_active_users
FROM events
WHERE timestamp >= today() - INTERVAL 30 DAY
GROUP BY date
ORDER BY date;

-- Retention analysis
SELECT
    signup_date,
    countIf(days_since_signup = 0) AS day_0,
    countIf(days_since_signup = 1) AS day_1,
    countIf(days_since_signup = 7) AS day_7,
    countIf(days_since_signup = 30) AS day_30
FROM (
    SELECT
        user_id,
        min(toDate(timestamp)) AS signup_date,
        toDate(timestamp) AS activity_date,
        dateDiff('day', signup_date, activity_date) AS days_since_signup
    FROM events
    GROUP BY user_id, activity_date
)
GROUP BY signup_date
ORDER BY signup_date DESC;
```

### Funnel Analysis

```sql
-- Conversion funnel
SELECT
    countIf(step = 'viewed_product') AS viewed,
    countIf(step = 'clicked_order') AS clicked,
    countIf(step = 'completed_order') AS completed,
    round(clicked / viewed * 100, 2) AS view_to_click_rate,
    round(completed / clicked * 100, 2) AS click_to_completion_rate
FROM (
    SELECT
        user_id,
        session_id,
        event_type AS step
    FROM events
    WHERE event_date = today()
)
GROUP BY session_id;
```

### Cohort Analysis

```sql
-- User cohorts by signup month
SELECT
    toStartOfMonth(signup_date) AS cohort,
    toStartOfMonth(activity_date) AS month,
    dateDiff('month', cohort, month) AS months_since_signup,
    count(DISTINCT user_id) AS active_users
FROM (
    SELECT
        user_id,
        min(toDate(timestamp)) OVER (PARTITION BY user_id) AS signup_date,
        toDate(timestamp) AS activity_date
    FROM events
)
GROUP BY cohort, month, months_since_signup
ORDER BY cohort, months_since_signup;
```

## Data Pipeline Patterns

### ETL Pattern

```typescript
// Extract, Transform, Load
async function etlPipeline() {
  // 1. Extract from source
  const rawData = await extractFromPostgres()

  // 2. Transform
  const transformed = rawData.map(row => ({
    date: new Date(row.created_at).toISOString().split('T')[0],
    product_id: row.product_slug,
    sales: parseFloat(row.total_sales),
    orders: parseInt(row.order_count)
  }))

  // 3. Load to ClickHouse
  await bulkInsertToClickHouse(transformed)
}

// Run periodically
setInterval(etlPipeline, 60 * 60 * 1000)  // Every hour
```

### Change Data Capture (CDC)

```typescript
// Listen to PostgreSQL changes and sync to ClickHouse
import { Client } from 'pg'

const pgClient = new Client({ connectionString: process.env.DATABASE_URL })

pgClient.query('LISTEN product_updates')

pgClient.on('notification', async (msg) => {
  const update = JSON.parse(msg.payload)

  await clickhouse.insert('product_updates', [
    {
      product_id: update.id,
      event_type: update.operation,  // INSERT, UPDATE, DELETE
      timestamp: new Date(),
      data: JSON.stringify(update.new_data)
    }
  ])
})
```

## Best Practices

### 1. Partitioning Strategy
- Partition by time (usually month or day)
- Avoid too many partitions (performance impact)
- Use DATE type for partition key

### 2. Ordering Key
- Put most frequently filtered columns first
- Consider cardinality (high cardinality first)
- Order impacts compression

### 3. Data Types
- Use smallest appropriate type (UInt32 vs UInt64)
- Use LowCardinality for repeated strings
- Use Enum for categorical data

### 4. Avoid
- SELECT * (specify columns)
- FINAL (merge data before query instead)
- Too many JOINs (denormalize for analytics)
- Small frequent inserts (batch instead)

### 5. Monitoring
- Track query performance
- Monitor disk usage
- Check merge operations
- Review slow query log

**Remember**: ClickHouse excels at analytical workloads. Design tables for your query patterns, batch inserts, and leverage materialized views for real-time aggregations.
