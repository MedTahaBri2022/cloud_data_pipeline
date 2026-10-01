import assert from 'node:assert/strict';
import { after, before, beforeEach, test } from 'node:test';
import { createApp } from '../src/app.js';

const stored = [];
const fakes = {
  rawOrders: {
    upsertMany: async (orders) => stored.push(...orders),
    ping: async () => {},
  },
  reports: {
    monthlyRevenue: async () => [{ year_month: '2026-01', revenue: 1200.5 }],
    topProducts: async (limit) =>
      Array.from({ length: limit }, (_, i) => ({ rank: i + 1 })),
    customerSegments: async () => [{ segment: 'loyal', customers: 3 }],
    pipelineStatus: async () => {
      throw new Error('warehouse is down');
    },
  },
};

const order = (overrides = {}) => ({
  order_id: 'O-1',
  customer_id: 'C001',
  status: 'placed',
  channel: 'web',
  ordered_at: '2026-01-10T10:00:00Z',
  items: [{ product_id: 'P001', quantity: 2, unit_price: 9.99 }],
  ...overrides,
});

let server;
let base;
const post = (path, body) =>
  fetch(`${base}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });

before(async () => {
  server = createApp(fakes).listen(0);
  await new Promise((resolve) => server.once('listening', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(() => server.close());
beforeEach(() => {
  stored.length = 0;
});

test('accepts a well-formed order', async () => {
  const response = await post('/orders', order());

  assert.equal(response.status, 202);
  assert.equal(stored.length, 1);
  assert.equal(stored[0].order_id, 'O-1');
});

test('stores business-invalid data: judging it is the pipeline’s job', async () => {
  const response = await post(
    '/orders',
    order({ customer_id: 'UNKNOWN', items: [{ product_id: 'P1', quantity: -3, unit_price: 1 }] }),
  );

  assert.equal(response.status, 202);
  assert.equal(stored.length, 1);
});

test('refuses a structurally broken order and says why', async () => {
  const response = await post('/orders', { order_id: 'O-2', extra: true });
  const body = await response.json();

  assert.equal(response.status, 400);
  assert.ok(body.errors.some((error) => error.startsWith('customer_id')));
  assert.equal(stored.length, 0);
});

test('a batch keeps its valid orders and reports the others by index', async () => {
  const response = await post('/orders/batch', {
    orders: [order(), { nope: true }, order({ order_id: 'O-3' })],
  });
  const body = await response.json();

  assert.equal(response.status, 207);
  assert.equal(body.accepted, 2);
  assert.deepEqual(
    body.rejected.map((entry) => entry.index),
    [1],
  );
  assert.deepEqual(
    stored.map((entry) => entry.order_id),
    ['O-1', 'O-3'],
  );
});

test('an oversized or empty batch is refused', async () => {
  const tooMany = { orders: Array.from({ length: 501 }, () => order()) };

  assert.equal((await post('/orders/batch', tooMany)).status, 400);
  assert.equal((await post('/orders/batch', { orders: [] })).status, 400);
  assert.equal(stored.length, 0);
});

test('invalid JSON is a 400, not a 500', async () => {
  const response = await post('/orders', '{"order_id": ');
  assert.equal(response.status, 400);
});

test('reports are served from the marts', async () => {
  const revenue = await (await fetch(`${base}/reports/monthly-revenue`)).json();
  const products = await (await fetch(`${base}/reports/top-products?limit=3`)).json();

  assert.deepEqual(revenue, [{ year_month: '2026-01', revenue: 1200.5 }]);
  assert.equal(products.length, 3);
});

test('report parameters are validated', async () => {
  for (const limit of ['0', '101', 'abc', '2.5']) {
    const response = await fetch(`${base}/reports/top-products?limit=${limit}`);
    assert.equal(response.status, 400, `limit=${limit}`);
  }
});

test('a failing store becomes an opaque 500', async (t) => {
  t.mock.method(console, 'error', () => {});
  const response = await fetch(`${base}/reports/pipeline`);
  const body = await response.json();

  assert.equal(response.status, 500);
  assert.deepEqual(body, { errors: ['Internal server error'] });
});

test('unknown routes return 404', async () => {
  assert.equal((await fetch(`${base}/nope`)).status, 404);
});
