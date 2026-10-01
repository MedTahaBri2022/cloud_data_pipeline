import { MongoClient } from 'mongodb';
import pg from 'pg';
import { createApp } from './app.js';
import { createRawOrderStore, createReportStore } from './stores.js';

const config = {
  port: Number(process.env.PORT ?? 8080),
  mongoUrl: process.env.MONGO_URL ?? 'mongodb://localhost:27018',
  mongoDatabase: process.env.MONGO_DATABASE ?? 'raw',
  warehouseDsn:
    process.env.WAREHOUSE_DSN ??
    'postgresql://pipeline:pipeline@localhost:54324/warehouse',
};

const mongo = new MongoClient(config.mongoUrl);
await mongo.connect();
const pool = new pg.Pool({ connectionString: config.warehouseDsn, max: 5 });

const rawOrders = createRawOrderStore(mongo.db(config.mongoDatabase));
await rawOrders.ensureIndexes();

const server = createApp({ rawOrders, reports: createReportStore(pool) }).listen(
  config.port,
  () => console.log(`ingestion-api listening on :${config.port}`),
);

// Finish in-flight requests, then release the connections.
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => {
    server.close(async () => {
      await Promise.allSettled([mongo.close(), pool.end()]);
      process.exit(0);
    });
  });
}
