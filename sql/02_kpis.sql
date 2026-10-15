-- Step 2: the numbers a restaurant manager reviews every Monday. Built only from the cleaned tables.
-- Weeks run Monday-Sunday. COGS (cost of goods sold) = opening inventory + purchases - closing inventory.

CREATE OR REPLACE TABLE daily AS
WITH sales AS (
    SELECT business_date,
           sum(net_sales)                                              AS net_sales,
           sum(net_sales) FILTER (category = 'Food')                   AS food_sales,
           sum(net_sales) FILTER (category = 'Beverage')               AS beverage_sales,
           sum(net_sales) FILTER (category = 'Alcohol')                AS alcohol_sales,
           -sum(net_sales) FILTER (is_void)                            AS voids
    FROM pos GROUP BY ALL),
labor AS (
    SELECT business_date, sum(hours) AS labor_hours, sum(wages) AS labor_cost,
           sum(hours) FILTER (role IN ('Line Cook', 'Sous Chef', 'Dishwasher')) AS kitchen_hours
    FROM shifts_paid GROUP BY ALL)
SELECT s.business_date,
       date_trunc('week', s.business_date)::DATE                       AS week_start,
       dayname(s.business_date)                                        AS weekday,
       isodow(s.business_date)                                         AS weekday_num,
       s.net_sales, s.food_sales, s.beverage_sales, s.alcohol_sales, s.voids,
       ps.covers, ps.checks,
       round(s.net_sales / nullif(ps.covers, 0), 2)                    AS avg_check_per_guest,
       round(l.labor_hours, 1)                                         AS labor_hours,
       l.labor_cost,
       round(l.labor_cost / nullif(s.net_sales, 0), 4)                 AS labor_pct,
       round(s.net_sales / nullif(l.labor_hours, 0), 2)                AS sales_per_labor_hour,
       round(ps.covers / nullif(l.labor_hours, 0), 2)                  AS guests_per_labor_hour
FROM sales s
LEFT JOIN pos_summary ps USING (business_date)
LEFT JOIN labor l USING (business_date);

CREATE OR REPLACE TABLE cogs_weekly AS
WITH weeks AS (SELECT DISTINCT week_start FROM daily),
cats AS (SELECT DISTINCT category FROM inventory),
bought AS (SELECT date_trunc('week', delivery_date)::DATE AS week_start, category, sum(amount) AS purchases
           FROM purchases GROUP BY ALL)
SELECT w.week_start, c.category,
       open_inv.value                                                   AS opening_inventory,
       coalesce(b.purchases, 0)                                         AS purchases,
       close_inv.value                                                  AS closing_inventory,
       open_inv.value + coalesce(b.purchases, 0) - close_inv.value      AS cogs
FROM weeks w CROSS JOIN cats c
LEFT JOIN bought b ON b.week_start = w.week_start AND b.category = c.category
LEFT JOIN inventory open_inv  ON open_inv.category = c.category  AND open_inv.count_date  = w.week_start - INTERVAL 1 DAY
LEFT JOIN inventory close_inv ON close_inv.category = c.category AND close_inv.count_date = w.week_start + INTERVAL 6 DAY;

CREATE OR REPLACE TABLE weekly AS
WITH d AS (
    SELECT week_start,
           count(*) AS days, sum(net_sales) AS net_sales, sum(food_sales) AS food_sales,
           sum(beverage_sales) + sum(alcohol_sales) AS bar_sales, sum(covers) AS covers,
           sum(labor_hours) AS labor_hours, sum(labor_cost) AS labor_cost, sum(voids) AS voids
    FROM daily GROUP BY ALL),
c AS (
    SELECT week_start,
           sum(cogs) FILTER (category IN ('Produce', 'Seafood', 'Meat', 'Dairy', 'Dry goods')) AS food_cogs,
           sum(cogs) FILTER (category IN ('Beverage', 'Alcohol'))                               AS bar_cogs
    FROM cogs_weekly GROUP BY ALL),
o AS (SELECT week_start, sum(overtime_hours) AS overtime_hours FROM shifts_paid GROUP BY ALL),
k AS (
    SELECT d.*, c.food_cogs, c.bar_cogs, o.overtime_hours,
           round(d.net_sales / d.covers, 2)                       AS avg_check_per_guest,
           round(d.labor_cost / d.net_sales, 4)                   AS labor_pct,
           round(d.net_sales / d.labor_hours, 2)                  AS sales_per_labor_hour,
           round(c.food_cogs / d.food_sales, 4)                   AS food_cost_pct,
           round(c.bar_cogs / d.bar_sales, 4)                     AS bar_cost_pct,
           round((d.labor_cost + c.food_cogs + c.bar_cogs) / d.net_sales, 4) AS prime_cost_pct
    FROM d JOIN c USING (week_start) JOIN o USING (week_start))
SELECT *,
       round(net_sales / lag(net_sales) OVER w - 1, 4)            AS sales_change_vs_prior_week,
       round(labor_pct - lag(labor_pct) OVER w, 4)                AS labor_pct_change_pts,
       round(food_cost_pct - lag(food_cost_pct) OVER w, 4)        AS food_cost_pct_change_pts,
       round(avg_check_per_guest - lag(avg_check_per_guest) OVER w, 2) AS avg_check_change
FROM k
WINDOW w AS (ORDER BY week_start)
ORDER BY week_start;

CREATE OR REPLACE TABLE items_weekly AS
SELECT date_trunc('week', business_date)::DATE AS week_start, item, category,
       sum(qty) FILTER (NOT is_void) AS qty_sold, sum(net_sales) AS net_sales,
       round(sum(net_sales) FILTER (NOT is_void) / nullif(sum(qty) FILTER (NOT is_void), 0), 2) AS avg_price
FROM pos GROUP BY ALL ORDER BY week_start, net_sales DESC;

CREATE OR REPLACE TABLE labor_by_role AS
SELECT business_date, week_start::DATE AS week_start, dayname(business_date) AS weekday, role, daypart,
       count(*) AS shifts, round(sum(hours), 1) AS hours, sum(wages) AS wages, round(sum(overtime_hours), 1) AS overtime_hours
FROM shifts_paid GROUP BY ALL ORDER BY business_date, role, daypart;

CREATE OR REPLACE TABLE vendor_weekly AS
SELECT date_trunc('week', delivery_date)::DATE AS week_start, vendor, category, sum(amount) AS purchases
FROM purchases GROUP BY ALL ORDER BY week_start, vendor;
