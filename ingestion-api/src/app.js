import express from 'express';
import { batchSchema, MAX_BATCH, partitionBatch } from './schema.js';

/**
 * Builds the HTTP application. The stores are injected, so the routes are
 * tested without MongoDB or PostgreSQL.
 *
 * @param {{
 *   rawOrders: { upsertMany(orders: object[]): Promise<number>, ping(): Promise<void> },
 *   reports: {
 *     monthlyRevenue(): Promise<object[]>,
 *     topProducts(limit: number): Promise<object[]>,
 *     customerSegments(): Promise<object[]>,
 *     pipelineStatus(): Promise<object>,
 *   },
 * }} dependencies
 */
export function createApp({ rawOrders, reports }) {
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '2mb' }));

  app.get('/health', async (_request, response) => {
    await rawOrders.ping();
    response.json({ status: 'ok' });
  });

  // ------------------------------------------------------------- ingestion

  app.post('/orders', async (request, response) => {
    const { accepted, rejected } = partitionBatch([request.body]);
    if (rejected.length) {
      return response.status(400).json({ errors: rejected[0].errors });
    }
    await rawOrders.upsertMany(accepted);
    response.status(202).json({ accepted: 1 });
  });

  app.post('/orders/batch', async (request, response) => {
    const batch = batchSchema.safeParse(request.body);
    if (!batch.success) {
      return response.status(400).json({
        errors: [`orders must be an array of 1 to ${MAX_BATCH} events`],
      });
    }
    // One malformed event does not sink the 499 others: the valid ones are
    // stored and the caller is told exactly which ones to fix.
    const { accepted, rejected } = partitionBatch(batch.data.orders);
    if (accepted.length) await rawOrders.upsertMany(accepted);
    response.status(rejected.length ? 207 : 202).json({
      accepted: accepted.length,
      rejected,
    });
  });

  // ------------------------------------------------------------- reporting

  app.get('/reports/monthly-revenue', async (_request, response) => {
    response.json(await reports.monthlyRevenue());
  });

  app.get('/reports/top-products', async (request, response) => {
    const limit = Number(request.query.limit ?? 10);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
      return response
        .status(400)
        .json({ errors: ['limit must be an integer between 1 and 100'] });
    }
    response.json(await reports.topProducts(limit));
  });

  app.get('/reports/customer-segments', async (_request, response) => {
    response.json(await reports.customerSegments());
  });

  app.get('/reports/pipeline', async (_request, response) => {
    response.json(await reports.pipelineStatus());
  });

  // --------------------------------------------------------------- errors

  app.use((_request, response) => {
    response.status(404).json({ errors: ['Not found'] });
  });

  // Express 5 forwards rejected promises here.
  app.use((error, _request, response, _next) => {
    if (error.type === 'entity.parse.failed') {
      return response.status(400).json({ errors: ['Body is not valid JSON'] });
    }
    if (error.type === 'entity.too.large') {
      return response.status(413).json({ errors: ['Body is too large'] });
    }
    console.error(error);
    response.status(500).json({ errors: ['Internal server error'] });
  });

  return app;
}
