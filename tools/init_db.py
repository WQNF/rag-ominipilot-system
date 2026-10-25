import os

import psycopg2
from psycopg2 import sql
import bcrypt
from dotenv import load_dotenv

load_dotenv()

# 数据库连接参数：优先读取 .env（容器内由 compose 注入 postgres 服务名）
DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "user": os.getenv("POSTGRES_USER", "omnipilot"),
    "password": os.getenv("POSTGRES_PASSWORD", "omnipilot123"),
    "database": os.getenv("POSTGRES_DB", "omnipilot"),
}

SQL_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS departments (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    department_id INTEGER REFERENCES departments(id),
    email VARCHAR(100),
    phone VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    model VARCHAR(50) NOT NULL,
    category VARCHAR(50),
    unit_price DECIMAL(10,2)
);

CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100),
    city VARCHAR(50),
    level VARCHAR(10) CHECK (level IN ('A','B','C'))
);

CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id),
    product_id INTEGER REFERENCES products(id),
    salesperson_id INTEGER REFERENCES employees(id),
    quantity INTEGER,
    total_amount DECIMAL(12,2),
    order_date DATE
);

CREATE TABLE IF NOT EXISTS app_users (
    username VARCHAR(64) PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL DEFAULT '',
    email VARCHAR(120) NOT NULL DEFAULT '',
    department VARCHAR(80) NOT NULL DEFAULT 'general',
    role VARCHAR(20) NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    hashed_password TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_documents (
    id VARCHAR(64) PRIMARY KEY,
    username VARCHAR(64) NOT NULL REFERENCES app_users(username) ON DELETE CASCADE,
    file_name VARCHAR(512) NOT NULL,
    stored_name VARCHAR(512) NOT NULL,
    file_type VARCHAR(32) NOT NULL,
    file_path TEXT NOT NULL,
    file_size BIGINT NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'ready',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(username, file_name)
);

CREATE INDEX IF NOT EXISTS idx_user_documents_username ON user_documents(username);
"""

SQL_INSERT_DATA = """
-- 部门
INSERT INTO departments (id, name) VALUES
(1, '销售部'), (2, '技术部'), (3, '市场部')
ON CONFLICT (id) DO NOTHING;

-- 员工
INSERT INTO employees (id, name, department_id, email, phone) VALUES
(1, '张伟', 1, 'zhangwei@starring.com', '13800138001'),
(2, '李娜', 1, 'lina@starring.com', '13800138002'),
(3, '王磊', 2, 'wanglei@starring.com', NULL)
ON CONFLICT (id) DO NOTHING;

-- 产品
INSERT INTO products (id, model, category, unit_price) VALUES
(1, 'StarBook-14', '笔记本电脑', 4999.00),
(2, 'StarBook-16', '笔记本电脑', 5999.00),
(3, 'StarTab-Pro', '平板电脑', 3499.00),
(4, 'StarWatch-X', '智能手表', 1299.00),
(5, 'StarBuds', '耳机', 399.00)
ON CONFLICT (id) DO NOTHING;

-- 客户
INSERT INTO customers (id, name, city, level) VALUES
(1, '北京星辰科技', '北京', 'A'),
(2, '上海云帆教育', '上海', 'A'),
(3, '成都熊猫文创', '成都', 'B'),
(4, '深圳迅捷物流', '深圳', 'B'),
(5, '杭州西湖传媒', '杭州', 'C')
ON CONFLICT (id) DO NOTHING;

-- 订单
INSERT INTO orders (id, customer_id, product_id, salesperson_id, quantity, total_amount, order_date) VALUES
(1, 1, 1, 1, 20, 20*4999.00, '2024-04-03'),
(2, 1, 2, 2, 50, 50*5999.00, '2024-04-05'),
(3, 2, 2, 2, 10, 10*5999.00, '2024-04-08'),
(4, 3, 3, 1, 15, 15*3499.00, '2024-04-12'),
(5, 4, 4, 1, 30, 30*1299.00, '2024-04-15'),
(6, 5, 5, 2, 100, 100*399.00, '2024-05-02'),
(7, 1, 1, 1, 25, 25*4999.00, '2024-05-10'),
(8, 2, 3, 2, 5, 5*3499.00, '2024-05-14'),
(9, 3, 2, 1, 8, 8*5999.00, '2024-05-18'),
(10,4, 4, 1, 20, 20*1299.00, '2024-05-22')
ON CONFLICT (id) DO NOTHING;
"""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def seed_app_users(cursor):
    users = [
        ("admin", "系统管理员", "admin@omnipilot.local", "系统管理", "admin", "admin123"),
        ("zhangwei", "张伟", "zhangwei@starring.com", "销售部", "user", "secret123"),
    ]
    for username, full_name, email, department, role, password in users:
        cursor.execute(
            """
            INSERT INTO app_users (
                username, full_name, email, department, role, hashed_password, is_active
            )
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (username) DO NOTHING
            """,
            (username, full_name, email, department, role, hash_password(password)),
        )

def init_database():
    print("正在连接 PostgreSQL...")
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    cursor = conn.cursor()

    print("创建表...")
    cursor.execute(SQL_CREATE_TABLES)

    print("插入示例数据...")
    cursor.execute(SQL_INSERT_DATA)

    print("初始化登录用户...")
    seed_app_users(cursor)

    cursor.close()
    conn.close()
    print("✅ 数据库初始化完成！")

if __name__ == "__main__":
    init_database()
