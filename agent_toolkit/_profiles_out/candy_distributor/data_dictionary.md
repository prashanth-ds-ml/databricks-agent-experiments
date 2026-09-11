# candy_distributor Data Dictionary

Auto-generated table/column profile for `candy_distributor`.

## candy_factories
- Rows: 5  |  Columns: 3
- Inferred primary key: `factory` (heuristic, not a declared constraint)

| Column | Kind | Null % | Distinct | Summary |
|---|---|---|---|---|
| factory (PK, inferred) | categorical | 0.0% | 5 | Secret Factory (1), Wicked Choccy's (1), Lot's O' Nuts (1), Sugar Shack (1), The Other Factory (1) |
| latitude | numerical | 0.0% | 5 | min=32.076176, max=48.11914, mean=37.9282, median=35.1175, stddev=6.7795 |
| longitude | numerical | 0.0% | 5 | min=-111.768036, max=-81.088371, mean=-93.9148, median=-90.565487, stddev=11.3486 |

## candy_products
- Rows: 15  |  Columns: 6
- Inferred primary key: `product_id` (heuristic, not a declared constraint)

| Column | Kind | Null % | Distinct | Summary |
|---|---|---|---|---|
| division | categorical | 0.0% | 3 | Sugar (6), Chocolate (5), Other (4) |
| product_name | categorical | 0.0% | 15 | Fun Dip (1), Laffy Taffy (1), SweeTARTS (1), Nerds (1), Wonka Bar - Fudge Mallows (1) |
| factory | categorical | 0.0% | 5 | Sugar Shack (5), Secret Factory (3), Lot's O' Nuts (3), Wicked Choccy's (2), The Other Factory (2) |
| product_id (PK, inferred) | categorical | 0.0% | 15 | SUG-FUN-75000 (1), SUG-LAF-25000 (1), SUG-SWE-91000 (1), SUG-NER-92000 (1), CHO-FUD-51000 (1) |
| unit_price | numerical | 0.0% | 10 | min=1.25, max=20.0, mean=4.462, median=3.49, stddev=4.7901 |
| unit_cost | numerical | 0.0% | 13 | min=0.6, max=10.0, mean=1.806, median=1.1, stddev=2.3449 |

## candy_sales
- Rows: 10194  |  Columns: 18
- Inferred primary key: `row_id` (heuristic, not a declared constraint)

| Column | Kind | Null % | Distinct | Summary |
|---|---|---|---|---|
| row_id (PK, inferred) | numerical | 0.0% | 10194 | min=1.0, max=10194.0, mean=5097.5, median=5096.0, stddev=2942.8987 |
| order_id | categorical | 0.0% | 8549 | 8549 unique values across 10194 rows; too high-cardinality to list (likely an identifier/free-text column) |
| order_date | temporal | 0.0% | 1242 | range: 2021-01-03 to 2024-12-30 |
| ship_date | temporal | 0.0% | 1338 | range: 2026-06-30 to 2030-06-28 |
| ship_mode | categorical | 0.0% | 4 | Standard Class (6120), Second Class (1979), First Class (1548), Same Day (547) |
| customer_id | numerical | 0.0% | 5044 | min=100006.0, max=192314.0, mean=134468.9612, median=133543.0, stddev=20231.483 |
| country_region | categorical | 0.0% | 2 | United States (9994), Canada (200) |
| city | categorical | 0.0% | 542 | 542 unique values across 10194 rows; too high-cardinality to list (likely an identifier/free-text column) |
| state_province | categorical | 0.0% | 59 | 59 unique values across 10194 rows; too high-cardinality to list (likely an identifier/free-text column) |
| postal_code | categorical | 0.0% | 654 | 654 unique values across 10194 rows; too high-cardinality to list (likely an identifier/free-text column) |
| division | categorical | 0.0% | 3 | Chocolate (9844), Other (310), Sugar (40) |
| region | categorical | 0.0% | 4 | Pacific (3253), Atlantic (2986), Interior (2335), Gulf (1620) |
| product_id | categorical | 0.0% | 15 | CHO-MIL-31000 (2137), CHO-SCR-58000 (2064), CHO-TRI-54000 (2015), CHO-FUD-51000 (1818), CHO-NUT-13000 (1810) |
| product_name | categorical | 0.0% | 15 | Wonka Bar - Milk Chocolate (2137), Wonka Bar -Scrumdiddlyumptious (2064), Wonka Bar - Triple Dazzle Caramel (2015), Wonka Bar - Fudge Mallows (1818), Wonka Bar - Nutty Crunch Surprise (1810) |
| sales | numerical | 0.0% | 88 | min=1.25, max=260.0, mean=13.9085, median=10.8, stddev=11.341 |
| units | numerical | 0.0% | 14 | min=1.0, max=14.0, mean=3.7918, median=3.0, stddev=2.2283 |
| gross_profit | numerical | 0.0% | 121 | min=0.25, max=130.0, mean=9.1665, median=7.47, stddev=6.6437 |
| cost | numerical | 0.0% | 95 | min=0.6, max=130.0, mean=4.7421, median=3.6, stddev=5.0616 |

## candy_targets
- Rows: 3  |  Columns: 2
- Inferred primary key: `division` (heuristic, not a declared constraint)

| Column | Kind | Null % | Distinct | Summary |
|---|---|---|---|---|
| division (PK, inferred) | categorical | 0.0% | 3 | Chocolate (1), Sugar (1), Other (1) |
| target | numerical | 0.0% | 3 | min=3000.0, max=27000.0, mean=15000.0, median=15000.0, stddev=12000.0 |

## uszips
- Rows: 33787  |  Columns: 18
- Inferred primary key: `zip` (heuristic, not a declared constraint)

| Column | Kind | Null % | Distinct | Summary |
|---|---|---|---|---|
| zip (PK, inferred) | numerical | 0.0% | 33787 | min=601.0, max=99929.0, mean=49705.5282, median=49721.0, stddev=27559.0583 |
| lat | numerical | 0.0% | 33412 | min=-14.21984, max=71.27434, mean=38.8008, median=39.47559, stddev=5.3877 |
| lng | numerical | 0.0% | 33657 | min=-176.62962, max=145.75349, mean=-90.9565, median=-88.18441, stddev=15.6664 |
| city | categorical | 0.0% | 17747 | 17747 unique values across 33787 rows; too high-cardinality to list (likely an identifier/free-text column) |
| state_id | categorical | 0.0% | 56 | 56 unique values across 33787 rows; too high-cardinality to list (likely an identifier/free-text column) |
| state_name | categorical | 0.0% | 56 | 56 unique values across 33787 rows; too high-cardinality to list (likely an identifier/free-text column) |
| zcta | categorical | 0.0% | 1 | true (33787) |
| parent_zcta | categorical | 100.0% | 0 |  |
| population | numerical | 0.05% | 15571 | min=0.0, max=134008.0, mean=9901.1701, median=2656.0, stddev=14908.642 |
| density | numerical | 0.05% | 9444 | min=0.0, max=60879.2, mean=510.0545, median=30.6, stddev=1944.7539 |
| county_fips | numerical | 0.0% | 3212 | min=1001.0, max=78030.0, mean=29989.6204, median=30029.0, stddev=15504.745 |
| county_name | categorical | 0.0% | 1913 | 1913 unique values across 33787 rows; too high-cardinality to list (likely an identifier/free-text column) |
| county_weights | categorical | 0.0% | 13395 | 13395 unique values across 33787 rows; too high-cardinality to list (likely an identifier/free-text column) |
| county_names_all | categorical | 0.0% | 12214 | 12214 unique values across 33787 rows; too high-cardinality to list (likely an identifier/free-text column) |
| county_fips_all | categorical | 0.0% | 11174 | 11174 unique values across 33787 rows; too high-cardinality to list (likely an identifier/free-text column) |
| imprecise | categorical | 0.0% | 8290 | 8290 unique values across 33787 rows; too high-cardinality to list (likely an identifier/free-text column) |
| military | categorical | 0.0% | 2527 | 2527 unique values across 33787 rows; too high-cardinality to list (likely an identifier/free-text column) |
| timezone | categorical | 0.0% | 429 | 429 unique values across 33787 rows; too high-cardinality to list (likely an identifier/free-text column) |
