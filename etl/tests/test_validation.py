from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from pipeline.validation import (
    Customer,
    Order,
    Product,
    Rejection,
    validate_customer,
    validate_order,
    validate_product,
)

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
CUSTOMERS = {"C001"}
PRODUCTS = {"P001", "P002"}


def order_doc(**overrides):
    doc = {
        "order_id": "O-1",
        "customer_id": "C001",
        "status": "Delivered",
        "channel": "web",
        "ordered_at": "2026-06-01T09:30:00Z",
        "items": [
            {"product_id": "P001", "quantity": 2, "unit_price": 19.99},
            {"product_id": "P002", "quantity": 1, "unit_price": "5.50"},
        ],
    }
    doc.update(overrides)
    return doc


def validate(doc):
    return validate_order(doc, CUSTOMERS, PRODUCTS, now=NOW)


class TestOrders:
    def test_valid_order_is_normalized(self):
        order = validate(order_doc())

        assert isinstance(order, Order)
        assert order.status == "delivered"
        assert order.ordered_at == datetime(2026, 6, 1, 9, 30, tzinfo=timezone.utc)
        assert [(i.product_id, i.quantity, i.unit_price) for i in order.items] == [
            ("P001", 2, Decimal("19.99")),
            ("P002", 1, Decimal("5.50")),
        ]

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"customer_id": "C999"}, "unknown customer C999"),
            ({"status": "teleported"}, "unknown status teleported"),
            ({"channel": "fax"}, "unknown channel fax"),
            ({"ordered_at": "yesterday"}, "ordered_at is not a valid timestamp"),
            ({"ordered_at": "2026-06-16T00:00:00Z"}, "ordered_at is in the future"),
            ({"items": []}, "an order needs at least one item"),
            (
                {"items": [{"product_id": "P404", "quantity": 1, "unit_price": 1}]},
                "item 1: unknown product P404",
            ),
            (
                {"items": [{"product_id": "P001", "quantity": 0, "unit_price": 1}]},
                "item 1: quantity must be a positive integer",
            ),
            (
                {"items": [{"product_id": "P001", "quantity": 1.5, "unit_price": 1}]},
                "item 1: quantity must be a positive integer",
            ),
            (
                {"items": [{"product_id": "P001", "quantity": 1, "unit_price": -4}]},
                "item 1: unit_price must be a non-negative amount",
            ),
            (
                {"items": [{"product_id": "P001", "quantity": 1, "unit_price": 1.999}]},
                "item 1: unit_price must be a non-negative amount",
            ),
        ],
    )
    def test_rejections(self, overrides, reason):
        result = validate(order_doc(**overrides))

        assert isinstance(result, Rejection)
        assert result.reasons == (reason,)
        assert result.record_id == "O-1"

    def test_an_order_with_one_bad_line_is_rejected_entirely(self):
        result = validate(
            order_doc(
                items=[
                    {"product_id": "P001", "quantity": 1, "unit_price": 10},
                    {"product_id": "P404", "quantity": 1, "unit_price": 10},
                ]
            )
        )
        assert isinstance(result, Rejection)

    def test_the_same_product_twice_is_rejected(self):
        item = {"product_id": "P001", "quantity": 1, "unit_price": 10}
        result = validate(order_doc(items=[item, item]))

        assert isinstance(result, Rejection)
        assert result.reasons == ("item 2: product P001 listed twice",)

    def test_every_problem_is_reported(self):
        result = validate({"order_id": "O-2", "items": "none"})

        assert isinstance(result, Rejection)
        assert len(result.reasons) == 5

    def test_rejected_payload_is_json_serializable(self):
        import json

        result = validate(order_doc(customer_id="C999", ingested_at=NOW, _id=object()))

        assert isinstance(result, Rejection)
        json.dumps(result.payload)

    def test_naive_timestamps_are_read_as_utc(self):
        order = validate(order_doc(ordered_at=datetime(2026, 6, 1, 9, 30)))

        assert isinstance(order, Order)
        assert order.ordered_at.tzinfo is timezone.utc

    def test_booleans_are_not_quantities(self):
        result = validate(
            order_doc(items=[{"product_id": "P001", "quantity": True, "unit_price": 1}])
        )
        assert isinstance(result, Rejection)


class TestReferenceData:
    def test_valid_customer(self):
        customer = validate_customer(
            {
                "customer_id": " C001 ",
                "name": "Ada Lovelace",
                "email": "Ada@Example.com",
                "country": "FR",
                "signup_date": "2025-11-02",
            }
        )
        assert customer == Customer(
            "C001", "Ada Lovelace", "ada@example.com", "FR", date(2025, 11, 2)
        )

    def test_invalid_customer_lists_all_reasons(self):
        result = validate_customer({"customer_id": "C002", "email": "nope"})

        assert isinstance(result, Rejection)
        assert result.source == "customers"
        assert set(result.reasons) == {
            "name is required",
            "email is not valid",
            "country is required",
            "signup_date must be YYYY-MM-DD",
        }

    def test_valid_product(self):
        product = validate_product(
            {
                "product_id": "P001",
                "name": "Notebook",
                "category": "Stationery",
                "unit_cost": "2.10",
                "list_price": "4.90",
            }
        )
        assert product == Product(
            "P001", "Notebook", "Stationery", Decimal("2.10"), Decimal("4.90")
        )

    @pytest.mark.parametrize("bad_price", ["", "abc", "-1", "NaN", "1.234"])
    def test_invalid_product_price(self, bad_price):
        result = validate_product(
            {
                "product_id": "P001",
                "name": "Notebook",
                "category": "Stationery",
                "unit_cost": "2.10",
                "list_price": bad_price,
            }
        )
        assert isinstance(result, Rejection)
        assert result.reasons == ("list_price must be a non-negative amount",)
