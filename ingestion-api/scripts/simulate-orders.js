/**
 * Plays the role of the upstream systems: sends order events to the
 * ingestion API in batches.
 *
 *   npm run simulate -- 5000            # 5000 new orders over the last 180 days
 *   npm run simulate -- 200 --updates   # 200 orders sent as placed, then again as delivered
 *
 * About 3% of the events are structurally fine but wrong for the business
 * (unknown product, zero quantity, date in the future...). The API accepts
 * them; the pipeline is expected to reject and report them.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const API_URL = process.env.API_URL ?? 'http://localhost:8080';
const count = Number(process.argv[2] ?? 1000);
const updates = process.argv.includes('--updates');
if (!Number.isInteger(count) || count < 1) {
  throw new Error('Usage: npm run simulate -- <number of events> [--updates]');
}

const dataDir = fileURLToPath(new URL('../../data/raw/', import.meta.url));
const readCsv = (name) =>
  readFileSync(`${dataDir}${name}`, 'utf8')
    .trim()
    .split('\n')
    .slice(1)
    .map((line) => line.split(','));

// Only rows the pipeline will accept are used as "known" ids.
const customers = readCsv('customers.csv')
  .filter((row) => row[2].includes('@') && row[1])
  .map((row) => row[0]);
const products = readCsv('products.csv')
  .filter((row) => !Number.isNaN(Number(row[3])))
  .map((row) => ({ id: row[0], price: Number(row[4]) }));

const pick = (values) => values[Math.floor(Math.random() * values.length)];
const DAY = 24 * 60 * 60 * 1000;
const run = Date.now().toString(36);

function makeOrder(index) {
  const orderedAt = new Date(Date.now() - Math.random() * 180 * DAY);
  const lines = new Map();
  for (let i = 0, n = 1 + Math.floor(Math.random() * 4); i < n; i++) {
    const product = pick(products);
    // Small discount or markup around the list price.
    const unitPrice = Number((product.price * (0.9 + Math.random() * 0.15)).toFixed(2));
    lines.set(product.id, {
      product_id: product.id,
      quantity: 1 + Math.floor(Math.random() * 3),
      unit_price: unitPrice,
    });
  }
  const order = {
    order_id: `O-${run}-${String(index).padStart(6, '0')}`,
    customer_id: pick(customers),
    status: pick(['placed', 'shipped', 'delivered', 'delivered', 'delivered', 'cancelled', 'returned']),
    channel: pick(['web', 'web', 'mobile', 'store']),
    ordered_at: orderedAt.toISOString(),
    items: [...lines.values()],
  };

  const roll = Math.random();
  if (roll < 0.008) order.items[0].product_id = 'P999';
  else if (roll < 0.016) order.items[0].quantity = 0;
  else if (roll < 0.022) order.customer_id = 'C9999';
  else if (roll < 0.027) order.ordered_at = new Date(Date.now() + 30 * DAY).toISOString();
  else if (roll < 0.03) order.status = 'lost';
  return order;
}

async function send(orders) {
  const response = await fetch(`${API_URL}/orders/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ orders }),
  });
  if (!response.ok && response.status !== 207) {
    throw new Error(`API answered ${response.status}: ${await response.text()}`);
  }
  return (await response.json()).accepted;
}

async function sendAll(events) {
  let accepted = 0;
  for (let start = 0; start < events.length; start += 250) {
    accepted += await send(events.slice(start, start + 250));
  }
  console.log(`${accepted} of ${events.length} events accepted by ${API_URL}`);
}

const orders = Array.from({ length: count }, (_, index) => makeOrder(index));
if (updates) {
  // Each order is sent twice, first as placed and then as delivered: the
  // second event must replace the first one, not create a second order.
  await sendAll(orders.map((order) => ({ ...order, status: 'placed' })));
  await sendAll(orders.map((order) => ({ ...order, status: 'delivered' })));
} else {
  await sendAll(orders);
}
