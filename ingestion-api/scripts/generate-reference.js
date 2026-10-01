/**
 * Writes the reference files the pipeline loads in batch:
 * data/raw/customers.csv and data/raw/products.csv.
 *
 * Seeded, so the committed files can be regenerated identically. A few rows
 * are invalid on purpose, to exercise the rejection path.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const OUT_DIR = fileURLToPath(new URL('../../data/raw/', import.meta.url));

// mulberry32: small deterministic PRNG.
function prng(seed) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const random = prng(2026);
const pick = (values) => values[Math.floor(random() * values.length)];
const pad = (n, width) => String(n).padStart(width, '0');

const FIRST = ['Amina', 'Youssef', 'Sara', 'Omar', 'Lina', 'Karim', 'Nora', 'Adam', 'Maya', 'Samir', 'Ines', 'Hugo', 'Lea', 'Noah', 'Emma'];
const LAST = ['Benali', 'Martin', 'Haddad', 'Dubois', 'Idrissi', 'Moreau', 'Tazi', 'Laurent', 'Alami', 'Girard', 'Naciri'];
const COUNTRIES = ['MA', 'MA', 'MA', 'FR', 'FR', 'ES', 'DE', 'BE'];

const CATALOG = {
  Stationery: ['Notebook A5', 'Gel Pen Set', 'Desk Planner', 'Sticky Notes', 'Highlighter Pack', 'Ring Binder', 'Sketch Pad', 'Fountain Pen'],
  Electronics: ['USB-C Hub', 'Wireless Mouse', 'Mechanical Keyboard', 'Webcam HD', 'Laptop Stand', 'Noise-Cancelling Headset', 'Portable SSD 1TB', 'Power Bank'],
  Home: ['Desk Lamp', 'Ceramic Mug', 'Storage Box', 'Wall Clock', 'Cushion', 'Scented Candle', 'Plant Pot', 'Coat Hanger Set'],
  Sports: ['Yoga Mat', 'Water Bottle', 'Resistance Bands', 'Jump Rope', 'Running Belt', 'Foam Roller', 'Gym Towel', 'Dumbbell 5kg'],
  Books: ['Clean Code', 'Designing Data-Intensive Applications', 'The Pragmatic Programmer', 'SQL Performance Explained', 'Kubernetes Up & Running', 'Terraform in Action', 'Data Pipelines Pocket Reference', 'Storytelling with Data'],
};
const BASE_PRICE = { Stationery: 6, Electronics: 45, Home: 15, Sports: 18, Books: 32 };

// --- customers
const customers = ['customer_id,name,email,country,signup_date'];
for (let i = 1; i <= 300; i++) {
  const first = pick(FIRST);
  const last = pick(LAST);
  const signup = new Date(Date.UTC(2024, 0, 1 + Math.floor(random() * 700)));
  customers.push(
    [
      `C${pad(i, 4)}`,
      `${first} ${last}`,
      `${first}.${last}${i}@example.com`.toLowerCase(),
      pick(COUNTRIES),
      signup.toISOString().slice(0, 10),
    ].join(','),
  );
}
// Invalid on purpose.
customers.push('C0301,Broken Email,not-an-email,MA,2025-03-01');
customers.push('C0302,,missing.name@example.com,FR,2025-13-40');

// --- products
const products = ['product_id,name,category,unit_cost,list_price'];
let productNumber = 0;
for (const [category, names] of Object.entries(CATALOG)) {
  for (const name of names) {
    productNumber++;
    const price = BASE_PRICE[category] * (0.6 + random() * 1.8);
    const cost = price * (0.45 + random() * 0.25);
    products.push(
      [`P${pad(productNumber, 3)}`, name, category, cost.toFixed(2), price.toFixed(2)].join(','),
    );
  }
}
// Invalid on purpose.
products.push('P041,Mystery Item,Home,abc,12.00');

mkdirSync(OUT_DIR, { recursive: true });
writeFileSync(`${OUT_DIR}customers.csv`, customers.join('\n') + '\n');
writeFileSync(`${OUT_DIR}products.csv`, products.join('\n') + '\n');
console.log(`${customers.length - 1} customers, ${products.length - 1} products written to ${OUT_DIR}`);
