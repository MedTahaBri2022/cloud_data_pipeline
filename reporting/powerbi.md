# Reporting with Power BI

The pipeline ends with a reporting layer in PostgreSQL (`marts` schema) and a
star schema (`warehouse` schema). Power BI reads them directly; no
transformation is needed in Power Query.

## The report in this repository

`reporting/powerbi/` holds a two-page report built on the star schema:

| File | What it is |
| --- | --- |
| `RetailAnalytics.pbix` | The report with its data, ready to open in Power BI Desktop |
| `RetailAnalytics.pbip` + `.SemanticModel/` + `.Report/` | The same report as a Power BI project: the model in TMDL and the report in PBIR, plain text that can be reviewed in a diff |
| `data/*.csv` | Snapshot of the warehouse the report imports (customer e-mails left out) |
| `export_data.py` | Rewrites the snapshot from the database |

| Overview | Products and customers |
| --- | --- |
| ![Overview page](../docs/powerbi-overview.png) | ![Products and customers page](../docs/powerbi-products-customers.png) |

**Model.** Star schema with `dim_date` marked as the date table (automatic
date tables are turned off), three one-to-many relationships to
`fact_order_items`, and the RFM segments joined one-to-one on the customer.
The amount columns are hidden; reports use the measures, which exclude
cancelled and returned orders like the SQL marts do. Revenue in the report,
788,497, is the sum of `marts.monthly_revenue` in PostgreSQL.

**Pages.** *Overview*: revenue, margin %, orders and average order value;
average daily revenue and margin per month (a daily average, so months of
different lengths compare); revenue by category and by channel; slicers on
channel and country. *Products and customers*: a product table with units,
revenue and margin %, customers per RFM segment, revenue per country.

The simulated data ends on 1 October 2026 at 03:49, so October holds a few
hours of orders: its point on the monthly chart is low for that reason.

**Open the project version.** The CSV files are read from the folder in the
`DataFolder` parameter. Set it to your clone (*Transform data → Edit
parameters*, with a trailing backslash), then *Refresh*. The `.pbix` needs
nothing: its data is embedded.

**Refresh the snapshot** after a pipeline run:

```bash
WAREHOUSE_DSN=postgresql://pipeline:pipeline@localhost:54324/warehouse \
    python reporting/powerbi/export_data.py
```

## Building it against the database instead

The steps below connect Power BI directly to PostgreSQL, for a report that
refreshes from the live warehouse.

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

The measures of the report in this repository
(`RetailAnalytics.SemanticModel/definition/tables/fact_order_items.tmdl`).
There the amount columns are named `line_revenue`, `line_cost` and
`line_margin`: a measure cannot have the name of a column of its table.

```DAX
Revenue =
    CALCULATE (
        SUM ( fact_order_items[line_revenue] ),
        NOT fact_order_items[status] IN { "cancelled", "returned" }
    )

Margin =
    CALCULATE (
        SUM ( fact_order_items[line_margin] ),
        NOT fact_order_items[status] IN { "cancelled", "returned" }
    )

Daily Revenue = DIVIDE ( [Revenue], COUNTROWS ( dim_date ) )

Margin % = DIVIDE ( [Margin], [Revenue] )

Orders =
    CALCULATE (
        DISTINCTCOUNT ( fact_order_items[order_id] ),
        NOT fact_order_items[status] IN { "cancelled", "returned" }
    )

Average Order Value = DIVIDE ( [Revenue], [Orders] )

Revenue Previous Month = CALCULATE ( [Revenue], DATEADD ( dim_date[full_date], -1, MONTH ) )

Revenue MoM % =
    VAR Previous = [Revenue Previous Month]
    RETURN IF ( NOT ISBLANK ( Previous ), DIVIDE ( [Revenue] - Previous, Previous ) )

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
