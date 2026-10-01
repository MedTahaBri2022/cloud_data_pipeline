/**
 * Raw landing zone: order events are stored as they arrive, one document per
 * order. A new event for the same order (status change, correction) replaces
 * the previous version and moves `ingested_at`, which is what the pipeline
 * uses to pick up only what changed since its last run.
 *
 * @param {import('mongodb').Db} db
 */
export function createRawOrderStore(db) {
  const collection = db.collection('raw_orders');

  return {
    async ensureIndexes() {
      await collection.createIndex({ ingested_at: 1 });
    },

    async upsertMany(orders) {
      const ingestedAt = new Date();
      const result = await collection.bulkWrite(
        orders.map((order) => ({
          updateOne: {
            filter: { _id: order.order_id },
            update: {
              $set: { ...order, ingested_at: ingestedAt },
              $setOnInsert: { first_seen_at: ingestedAt },
            },
            upsert: true,
          },
        })),
        { ordered: false },
      );
      return result.upsertedCount + result.modifiedCount;
    },

    async ping() {
      await db.command({ ping: 1 });
    },
  };
}

/**
 * Read side: the reporting layer built by the pipeline, exposed as JSON.
 * NUMERIC columns are cast in SQL so clients receive numbers, not strings.
 *
 * @param {import('pg').Pool} pool
 */
export function createReportStore(pool) {
  const rows = async (sql, params = []) => (await pool.query(sql, params)).rows;

  return {
    monthlyRevenue: () =>
      rows(`
        SELECT year_month, orders::int, units::int,
               revenue::float, margin::float, margin_pct::float
          FROM marts.monthly_revenue
         ORDER BY year_month`),

    topProducts: (limit) =>
      rows(
        `SELECT revenue_rank::int AS rank, product_id, name, category,
                orders::int, units::int, revenue::float, margin::float
           FROM marts.product_performance
          ORDER BY revenue_rank, product_id
          LIMIT $1`,
        [limit],
      ),

    customerSegments: () =>
      rows(`
        SELECT segment, count(*)::int AS customers,
               round(avg(revenue), 2)::float AS average_revenue,
               sum(revenue)::float AS revenue
          FROM marts.customer_segments
         GROUP BY segment
         ORDER BY revenue DESC`),

    async pipelineStatus() {
      const [[watermark], rejected, checks] = await Promise.all([
        rows(`SELECT max(position) AS position FROM etl.watermarks`),
        rows(`
          SELECT source, count(*)::int AS records
            FROM etl.rejected_records
           GROUP BY source
           ORDER BY source`),
        rows(`
          SELECT DISTINCT ON (check_name) check_name, passed, observed, checked_at
            FROM etl.quality_results
           ORDER BY check_name, checked_at DESC`),
      ]);
      return { ordersLoadedUpTo: watermark.position, rejected, checks };
    },
  };
}
