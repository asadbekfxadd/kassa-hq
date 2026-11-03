"""Нагрузочные данные прямо в PostgreSQL: 100 магазинов, 5000 товаров, остатки, 90 дней продаж, клиенты, сотрудники."""
import psycopg, time, sys
N_STORES, N_PROD, DAYS, CHECKS = 100, 5000, 90, int(sys.argv[1]) if len(sys.argv) > 1 else 60
t0 = time.time()
c = psycopg.connect("host=/var/tmp/pgtest port=5433 dbname=hqt user=postgres", autocommit=True)
x = lambda q, p=(): c.execute(q, p)
x("""INSERT INTO stores(uid,name,address,token,created_at,last_seen,info,active)
     SELECT md5('st'||g), 'Магазин '||lpad(g::text,3,'0'), 'Ташкент', md5('tok'||g), '2026-01-01 00:00:00', to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
     '{"customers_debt":100000,"suppliers_debt":500000,"cash_in_drawer":300000,"stock_value":50000000,"shift":true}', 1 FROM generate_series(1,%s) g""", (N_STORES,))
x("""INSERT INTO products(uid,name,barcode,category,brand,cost_price,price,min_stock,active,updated_at,spic)
     SELECT md5('p'||g), 'Товар '||g, (4780000000000+g)::text, 'Категория '||(g%%40), 'Бренд '||(g%%120), 1000+(g%%97)*300, (1000+(g%%97)*300)*1.25,
     CASE WHEN g%%10=0 THEN 5 ELSE 0 END, 1, '2026-01-01 00:00:00', lpad(g::text,17,'1') FROM generate_series(1,%s) g""", (N_PROD,))
# остатки: каждый магазин — 3000 товаров
x("""INSERT INTO stock(store_id,product_uid,qty,updated_at)
     SELECT s.id, md5('p'||g), (abs(hashtext(s.id||'-'||g))%%80)-5, '2026-11-01 00:00:00'
     FROM stores s, generate_series(1,3000) g""")
print('stores/products/stock', round(time.time() - t0, 1)); t0 = time.time()
x("""INSERT INTO customers(uid,name,phone,phone_key,first_store_id,created_at,updated_at)
     SELECT md5('c'||g), 'Клиент '||g, '99890'||lpad(g::text,7,'0'), '90'||lpad(g::text,7,'0'), 1+g%%100, '2026-01-01 00:00:00', '2026-01-01 00:00:00'
     FROM generate_series(1,50000) g""")
x("""INSERT INTO customer_stores(customer_id,store_id,till_uid,bonus,debt,updated_at)
     SELECT id, 1+id%%100, uid, id%%500, CASE WHEN id%%50=0 THEN 20000 ELSE 0 END, '2026-11-01 00:00:00' FROM customers""")
x("""INSERT INTO employees(uid,store_id,full_name,position,status,till_role,source,salary,created_at,updated_at)
     SELECT md5('e'||g), 1+g%%100, 'Кассир '||g, 'Кассир', 'active', 'cashier', 'hq', 4000000, '2026-01-01', '2026-01-01' FROM generate_series(1,300) g""")
# продажи: 100 магазинов × 90 дней × CHECKS чеков, 3 позиции
x(f"""INSERT INTO sales(uid,store_id,local_id,created_at,operation,total,cash,card,debt,discount,cashier,fiscal_status,qr,cashier_uid,customer_uid)
     SELECT md5('s'||st||'-'||d||'-'||k), st, k, to_char(timestamp '2026-11-03 00:00:00' - (d||' days')::interval + ((8*60 + (k*731)%%(14*60))||' minutes')::interval, 'YYYY-MM-DD HH24:MI:SS'),
       CASE WHEN k%%50=0 THEN 'refund' ELSE 'sale' END, 0, 0, 0, 0, 0, 'Кассир', 'sent', 0, md5('e'||(st+100*(k%%3))),
       CASE WHEN k%%3=0 THEN md5('c'||(1+abs(hashtext(st||'-'||d||'-'||k))%%50000)) END
     FROM generate_series(1,{N_STORES}) st, generate_series(0,{DAYS - 1}) d, generate_series(1,{CHECKS}) k""")
print('sales', round(time.time() - t0, 1)); t0 = time.time()
x("""INSERT INTO sale_items(sale_uid,product_uid,name,qty,price,total,cost_price)
     SELECT s.uid, md5('p'||p), 'Товар '||p, 1+(s.id+j)%%3, (1000+(p%%97)*300)*1.25, (1+(s.id+j)%%3)*(1000+(p%%97)*300)*1.25, 1000+(p%%97)*300
     FROM sales s, generate_series(1,3) j, LATERAL (SELECT 1+abs(hashtext(s.uid||j))%%3000 AS p) pp""")
x("UPDATE sales s SET total=t.v, cash=t.v FROM (SELECT sale_uid, SUM(total) v FROM sale_items GROUP BY sale_uid) t WHERE t.sale_uid=s.uid")
print('items', round(time.time() - t0, 1)); t0 = time.time()
x("ANALYZE")
for t in ('stores', 'products', 'stock', 'sales', 'sale_items', 'customers', 'employees'):
    print(t, c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
