import { z } from 'zod';

/**
 * Structural contract of an order event. The API only checks the *shape*:
 * business rules (does the customer exist? is the quantity positive?) belong
 * to the pipeline, which keeps and reports what it rejects. Refusing those
 * events here would lose the evidence that a producer sends bad data.
 */
export const orderSchema = z
  .object({
    order_id: z.string().trim().min(1).max(64),
    customer_id: z.string().trim().max(64),
    status: z.string().trim().max(32),
    channel: z.string().trim().max(32),
    ordered_at: z.string().trim().max(40),
    items: z
      .array(
        z
          .object({
            product_id: z.string().trim().max(64),
            quantity: z.number(),
            unit_price: z.number(),
          })
          .strict(),
      )
      .max(200),
  })
  .strict();

export const MAX_BATCH = 500;

export const batchSchema = z.object({
  orders: z.array(z.unknown()).min(1).max(MAX_BATCH),
});

/** Splits a batch into storable orders and per-index validation errors. */
export function partitionBatch(candidates) {
  const accepted = [];
  const rejected = [];
  candidates.forEach((candidate, index) => {
    const result = orderSchema.safeParse(candidate);
    if (result.success) {
      accepted.push(result.data);
    } else {
      rejected.push({
        index,
        errors: result.error.issues.map(
          (issue) => `${issue.path.join('.') || 'order'}: ${issue.message}`,
        ),
      });
    }
  });
  return { accepted, rejected };
}
