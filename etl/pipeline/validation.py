"""Validation rules for every source.

Pure functions: no database, no network. Each returns either a clean,
typed record or the list of reasons why the input was rejected, so rejected
data can be reported back to its owner instead of being silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

ORDER_STATUSES = {"placed", "shipped", "delivered", "cancelled", "returned"}
ORDER_CHANNELS = {"web", "mobile", "store"}

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str
    email: str
    country: str
    signup_date: date


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    category: str
    unit_cost: Decimal
    list_price: Decimal


@dataclass(frozen=True)
class OrderItem:
    product_id: str
    quantity: int
    unit_price: Decimal


@dataclass(frozen=True)
class Order:
    order_id: str
    customer_id: str
    status: str
    channel: str
    ordered_at: datetime
    items: tuple[OrderItem, ...]


@dataclass(frozen=True)
class Rejection:
    source: str
    record_id: str
    reasons: tuple[str, ...]
    payload: Mapping[str, Any]


def _text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    return value.strip() if isinstance(value, str) else ""


def _money(value: Any) -> Decimal | None:
    """Parses a non-negative amount with at most two decimals."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    if amount != amount.quantize(Decimal("0.01")):
        return None
    return amount


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    # Naive timestamps are taken as UTC, the convention of the sources.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def validate_customer(row: Mapping[str, Any]) -> Customer | Rejection:
    reasons: list[str] = []
    customer_id = _text(row, "customer_id")
    name = _text(row, "name")
    email = _text(row, "email").lower()
    country = _text(row, "country")

    if not customer_id:
        reasons.append("customer_id is required")
    if not name:
        reasons.append("name is required")
    if not _EMAIL.match(email):
        reasons.append("email is not valid")
    if not country:
        reasons.append("country is required")

    signup_date: date | None = None
    try:
        signup_date = date.fromisoformat(_text(row, "signup_date"))
    except ValueError:
        reasons.append("signup_date must be YYYY-MM-DD")

    if reasons or signup_date is None:
        return Rejection("customers", customer_id or "?", tuple(reasons), dict(row))
    return Customer(customer_id, name, email, country, signup_date)


def validate_product(row: Mapping[str, Any]) -> Product | Rejection:
    reasons: list[str] = []
    product_id = _text(row, "product_id")
    name = _text(row, "name")
    category = _text(row, "category")
    unit_cost = _money(row.get("unit_cost"))
    list_price = _money(row.get("list_price"))

    if not product_id:
        reasons.append("product_id is required")
    if not name:
        reasons.append("name is required")
    if not category:
        reasons.append("category is required")
    if unit_cost is None:
        reasons.append("unit_cost must be a non-negative amount")
    if list_price is None:
        reasons.append("list_price must be a non-negative amount")

    if reasons or unit_cost is None or list_price is None:
        return Rejection("products", product_id or "?", tuple(reasons), dict(row))
    return Product(product_id, name, category, unit_cost, list_price)


def validate_order(
    doc: Mapping[str, Any],
    known_customers: frozenset[str] | set[str],
    known_products: frozenset[str] | set[str],
    now: datetime | None = None,
) -> Order | Rejection:
    """Validates one raw order document.

    An order is accepted or rejected as a whole: loading only its valid lines
    would understate its revenue without anyone noticing.
    """
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    order_id = _text(doc, "order_id")
    customer_id = _text(doc, "customer_id")
    status = _text(doc, "status").lower()
    channel = _text(doc, "channel").lower()
    ordered_at = _timestamp(doc.get("ordered_at"))

    if not order_id:
        reasons.append("order_id is required")
    if customer_id not in known_customers:
        reasons.append(f"unknown customer {customer_id or '(empty)'}")
    if status not in ORDER_STATUSES:
        reasons.append(f"unknown status {status or '(empty)'}")
    if channel not in ORDER_CHANNELS:
        reasons.append(f"unknown channel {channel or '(empty)'}")
    if ordered_at is None:
        reasons.append("ordered_at is not a valid timestamp")
    elif ordered_at > now:
        reasons.append("ordered_at is in the future")

    raw_items = doc.get("items")
    items: list[OrderItem] = []
    if not isinstance(raw_items, list) or not raw_items:
        reasons.append("an order needs at least one item")
    else:
        seen: set[str] = set()
        for position, raw in enumerate(raw_items, start=1):
            if not isinstance(raw, Mapping):
                reasons.append(f"item {position}: not an object")
                continue
            product_id = _text(raw, "product_id")
            quantity = raw.get("quantity")
            unit_price = _money(raw.get("unit_price"))

            if product_id not in known_products:
                reasons.append(f"item {position}: unknown product {product_id or '(empty)'}")
            elif product_id in seen:
                reasons.append(f"item {position}: product {product_id} listed twice")
            seen.add(product_id)
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                reasons.append(f"item {position}: quantity must be a positive integer")
            if unit_price is None:
                reasons.append(f"item {position}: unit_price must be a non-negative amount")

            if isinstance(quantity, int) and unit_price is not None:
                items.append(OrderItem(product_id, quantity, unit_price))

    if reasons or ordered_at is None:
        return Rejection("orders", order_id or "?", tuple(reasons), _jsonable(doc))
    return Order(order_id, customer_id, status, channel, ordered_at, tuple(items))


def _jsonable(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Raw documents may contain datetimes or ObjectIds: keep them printable."""

    def convert(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): convert(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value)

    return convert(doc)
