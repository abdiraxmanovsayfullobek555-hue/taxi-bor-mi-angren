import os, sqlite3, time
from contextlib import contextmanager

DB_PATH = os.getenv('DB_PATH', 'taxi_bor_mi.db')

@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=30000')
    try:
        yield con
    finally:
        con.close()

def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS customers (
            tg_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            lang TEXT NOT NULL DEFAULT 'uz',
            route TEXT NOT NULL,
            blocked INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS drivers (
            tg_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            car_model TEXT NOT NULL,
            plate TEXT NOT NULL,
            route TEXT NOT NULL,
            license_file TEXT,
            tech_file TEXT,
            car_file TEXT,
            approved INTEGER NOT NULL DEFAULT 0,
            online INTEGER NOT NULL DEFAULT 0,
            blocked INTEGER NOT NULL DEFAULT 0,
            active_orders INTEGER NOT NULL DEFAULT 0,
            rating_sum INTEGER NOT NULL DEFAULT 0,
            rating_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            driver_id INTEGER,
            route TEXT NOT NULL,
            text TEXT NOT NULL,
            lat REAL,
            lon REAL,
            status TEXT NOT NULL DEFAULT 'searching',
            excluded_driver INTEGER,
            no_answer_until REAL,
            created_at REAL NOT NULL,
            accepted_at REAL,
            finished_at REAL
        );
        CREATE TABLE IF NOT EXISTS ratings (
            order_id INTEGER PRIMARY KEY,
            driver_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            created_at REAL NOT NULL
        );
        ''')

def get_customer(tg_id):
    with db() as c: return c.execute('SELECT * FROM customers WHERE tg_id=?',(tg_id,)).fetchone()

def save_customer(tg_id,name,phone,lang,route):
    with db() as c:
        c.execute('''INSERT INTO customers(tg_id,name,phone,lang,route,created_at)
                     VALUES(?,?,?,?,?,?)
                     ON CONFLICT(tg_id) DO UPDATE SET name=excluded.name,phone=excluded.phone,lang=excluded.lang''',
                  (tg_id,name,phone,lang,route,time.time()))

def get_driver(tg_id):
    with db() as c: return c.execute('SELECT * FROM drivers WHERE tg_id=?',(tg_id,)).fetchone()

def save_driver(data):
    with db() as c:
        c.execute('''INSERT INTO drivers(tg_id,name,phone,car_model,plate,route,license_file,tech_file,car_file,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(tg_id) DO UPDATE SET name=excluded.name,phone=excluded.phone,car_model=excluded.car_model,
                     plate=excluded.plate,route=excluded.route,license_file=excluded.license_file,tech_file=excluded.tech_file,
                     car_file=excluded.car_file,approved=0,online=0,blocked=0''',
                  (data['tg_id'],data['name'],data['phone'],data['car_model'],data['plate'],data['route'],data.get('license_file'),data.get('tech_file'),data.get('car_file'),time.time()))

def list_pending_drivers():
    with db() as c: return c.execute('SELECT * FROM drivers WHERE approved=0 AND blocked=0 ORDER BY created_at').fetchall()

def set_driver_approved(tg_id,approved):
    with db() as c: c.execute('UPDATE drivers SET approved=?, online=0 WHERE tg_id=?',(int(approved),tg_id))

def set_driver_blocked(tg_id,blocked):
    with db() as c: c.execute('UPDATE drivers SET blocked=?, online=0 WHERE tg_id=?',(int(blocked),tg_id))
    with db() as c: c.execute('UPDATE customers SET blocked=? WHERE tg_id=?',(int(blocked),tg_id))

def set_driver_online(tg_id,online):
    with db() as c: c.execute('UPDATE drivers SET online=? WHERE tg_id=? AND approved=1 AND blocked=0',(int(online),tg_id))

def create_order(customer_id,route,text):
    with db() as c:
        cur=c.execute('INSERT INTO orders(customer_id,route,text,created_at) VALUES(?,?,?,?)',(customer_id,route,text,time.time()))
        return cur.lastrowid

def set_order_location(order_id,lat,lon):
    with db() as c: c.execute('UPDATE orders SET lat=?,lon=? WHERE id=?',(lat,lon,order_id))

def latest_open_order(customer_id):
    with db() as c: return c.execute("SELECT * FROM orders WHERE customer_id=? AND status IN ('searching','accepted','no_answer') ORDER BY id DESC LIMIT 1",(customer_id,)).fetchone()

def get_order(order_id):
    with db() as c: return c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()

def eligible_drivers(route, excluded_driver=None):
    with db() as c:
        return c.execute("SELECT * FROM drivers WHERE approved=1 AND blocked=0 AND online=1 AND route=? AND active_orders<4 AND (? IS NULL OR tg_id<>?) ORDER BY active_orders, rating_count DESC",(route,excluded_driver,excluded_driver)).fetchall()

def get_customer(tg_id):
    with db() as c: return c.execute('SELECT * FROM customers WHERE tg_id=?',(tg_id,)).fetchone()

def claim_order(order_id,driver_id):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        order=c.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
        driver=c.execute('SELECT * FROM drivers WHERE tg_id=?', (driver_id,)).fetchone()
        if not order or not driver or order['status']!='searching' or driver['active_orders']>=4 or driver['route']!=order['route'] or not driver['online'] or not driver['approved'] or driver['blocked']:
            c.rollback(); return None
        c.execute("UPDATE orders SET driver_id=?,status='accepted',accepted_at=? WHERE id=?",(driver_id,time.time(),order_id))
        c.execute('UPDATE drivers SET active_orders=active_orders+1 WHERE tg_id=?',(driver_id,))
        c.commit()
        return c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()

def finish_order(order_id,driver_id):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        o=c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
        if not o or o['driver_id']!=driver_id or o['status']!='accepted': c.rollback(); return False
        c.execute("UPDATE orders SET status='finished',finished_at=? WHERE id=?",(time.time(),order_id))
        c.execute('UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?',(driver_id,))
        c.commit(); return True

def start_no_answer(order_id,driver_id):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        o=c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
        if not o or o['driver_id']!=driver_id or o['status']!='accepted': c.rollback(); return None
        until=time.time()+60
        c.execute("UPDATE orders SET status='no_answer',no_answer_until=? WHERE id=?",(until,order_id))
        c.execute('UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?',(driver_id,))
        c.commit(); return until

def reopen_order(order_id):
    with db() as c:
        o=c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
        if not o or o['status']!='no_answer': return False
        c.execute("UPDATE orders SET status='searching',driver_id=NULL,no_answer_until=NULL,excluded_driver=? WHERE id=?",(o['driver_id'],order_id)); return True

def cancel_order(order_id):
    with db() as c: c.execute("UPDATE orders SET status='cancelled',no_answer_until=NULL WHERE id=? AND status='no_answer'",(order_id,))

def rate_order(order_id,rating):
    with db() as c:
        o=c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
        if not o or o['status']!='finished' or not o['driver_id']: return False
        try: c.execute('INSERT INTO ratings(order_id,driver_id,rating,created_at) VALUES(?,?,?,?)',(order_id,o['driver_id'],rating,time.time()))
        except sqlite3.IntegrityError: return False
        c.execute('UPDATE drivers SET rating_sum=rating_sum+?,rating_count=rating_count+1 WHERE tg_id=?',(rating,o['driver_id'])); return True

def stats():
    with db() as c:
        return {
            'customers':c.execute('SELECT COUNT(*) n FROM customers').fetchone()['n'],
            'drivers':c.execute('SELECT COUNT(*) n FROM drivers').fetchone()['n'],
            'online':c.execute('SELECT COUNT(*) n FROM drivers WHERE online=1 AND approved=1 AND blocked=0').fetchone()['n'],
            'pending':c.execute('SELECT COUNT(*) n FROM drivers WHERE approved=0 AND blocked=0').fetchone()['n'],
            'orders':c.execute('SELECT COUNT(*) n FROM orders').fetchone()['n'],
            'active':c.execute("SELECT COUNT(*) n FROM orders WHERE status IN ('searching','accepted','no_answer')").fetchone()['n'],
            'finished':c.execute("SELECT COUNT(*) n FROM orders WHERE status='finished'").fetchone()['n'],
            'cancelled':c.execute("SELECT COUNT(*) n FROM orders WHERE status='cancelled'").fetchone()['n'],
        }
