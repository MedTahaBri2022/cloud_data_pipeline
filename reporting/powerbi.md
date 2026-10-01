# Reporting with Power BI

The pipeline ends with a reporting layer in PostgreSQL (`marts` schema) and a
star schema (`warehouse` schema). Power BI reads them directly; no
transformation is needed in Power Query.

> This repository contains the data model and the measures, not a `.pbix`
> file: a report is a binary built in Power BI Desktop against your own
> database. The steps below rebuild it in a few minutes.

## 1. Connect

*Get data → PostgreSQL database*

| Setting  | Local docker-compose value |
| -------- | -------------------------- |
| Server   | `localhost:54324`          |
| Database | `warehouse`                |
| Mode     | Import                     |
| User     | `pipeline`                 |

For anything other than a local demo, create a read-only role and use it:

```sql
CREATE ROLE bi_reader LOGIN PASSWORD '...';
GRANT USAGE ON SCHEMA warehouse, marts TO bi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA warehouse, marts TO bi_reader;
```

## 2. Choose the tables

**Quick dashboards** – the pre-aggregated marts, refreshed by the pipeline:

| Table                            | Grain                 | Typical visual                   |
| -------------------------------- | --------------------- | -------------------------------- |
| `marts.monthly_revenue`          | month                 | line chart revenue / margin      |
| `marts.product_performance`      | product               | bar chart top N, category matrix |
| `marts.customer_segments`        | customer              | donut by segment, table          |
| `marts.sales_by_country_channel` | country x channel     | map, stacked bars                |

**Self-service analysis** – the star schema, for slicing by any attribute:

```
dim_date ──┐
dim_customer ──┼──< fact_order_items
dim_product ──┘
```

Relationships (all one-to-many, single direction, dimension filters fact):

| From (one)                  | To (many)                       |
| --------------------------- | ------------------------------- |
| `dim_date[date_key]`        | `fact_order_items[date_key]`    |
| `dim_customer[customer_key]`| `fact_order_items[customer_key]`|
| `dim_product[product_key]`  | `fact_order_items[product_key]` |

Mark `dim_date` as the date table on `full_date`.

## 3. Measures (DAX)

Starting point for the model; written against the schema above, to be checked
in Power BI Desktop when the report is built.

```DAX
Revenue =
    CALCULATE (
        SUM ( fact_order_items[revenue] ),
        NOT fact_order_items[status] IN { "cancelled", "returned" }
    )

Margin =
    CALCULATE (
        SUM ( fact_order_items[margin] ),
        NOT fact_order_items[status] IN { "cancelled", "returned" }
    )

Margin % = DIVIDE ( [Margin], [Revenue] )

Orders =
    CALCULATE (
        DISTINCTCOUNT ( fact_order_items[order_id] ),
        NOT fact_order_items[status] IN { "cancelled", "returned" }
    )

Average Order Value = DIVIDE ( [Revenue], [Orders] )

Revenue MoM % =
    VAR Previous = CALCULATE ( [Revenue], DATEADD ( dim_date[full_date], -1, MONTH ) )
    RETURN DIVIDE ( [Revenue] - Previous, Previous )

Return Rate =
    DIVIDE (
        CALCULATE (
            DISTINCTCOUNT ( fact_order_items[order_id] ),
            fact_order_items[status] = "returned"
        ),
        DISTINCTCOUNT ( fact_order_items[order_id] )
    )
```

The exclusion of cancelled and returned orders is the same rule as in the SQL
marts (`sql/003_marts.sql`), so a figure built from the star schema matches the
same figure read from a mart.

## 4. Refresh

The marts are materialized views refreshed by the last task of the DAG. Schedule
the Power BI dataset refresh after the pipeline's hourly run (through an
on-premises data gateway if the database is not publicly reachable).

## Without Power BI

The same marts are exposed as JSON by the ingestion API:
`/reports/monthly-revenue`, `/reports/top-products?limit=10`,
`/reports/customer-segments`, `/reports/pipeline`.
