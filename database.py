
import sqlite3
from datetime import datetime

DB_NAME = "taxi.db"


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            language TEXT NOT NULL,
            route TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            route TEXT NOT NULL,
            text TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    """)

    conn.commit()
    conn.close()


def get_customer(telegram_id):
    conn = get_db()
    customer = conn.execute(
        "SELECT * FROM customers WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()
    conn.close()
    return customer


def create_customer(telegram_id, name, phone, language, route):
    conn = get_db()

    conn.execute("""
        INSERT INTO customers
        (telegram_id, name, phone, language, route, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        telegram_id,
        name,
        phone,
        language,
        route,
        datetime.now().isoformat()
    ))

    conn.commit()
    conn.close()


def create_order(customer_id, route, text, latitude=None, longitude=None):
    conn = get_db()

    cur = conn.execute("""
        INSERT INTO orders
        (customer_id, route, text, latitude, longitude, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'new', ?)
    """, (
        customer_id,
        route,
        text,
        latitude,
        longitude,
        datetime.now().isoformat()
    ))

    order_id = cur.lastrowid

    conn.commit()
    conn.close()

    return order_id


def get_customer_orders(customer_id):
    conn = get_db()

    orders = conn.execute("""
        SELECT *
        FROM orders
        WHERE customer_id = ?
        ORDER BY id DESC
    """, (customer_id,)).fetchall()

    conn.close()
    return orders


def update_order_status(order_id, status):
    conn = get_db()

    conn.execute(
        "UPDATE orders SET status = ? WHERE id = ?",
        (status, order_id)
    )

    conn.commit()
    conn.close()
