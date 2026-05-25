-- Тестовые данные для PostgreSQL
-- БД testdb создаётся автоматически через POSTGRES_DB

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    role VARCHAR(50) DEFAULT 'user',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    category VARCHAR(100),
    stock INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    product_id INTEGER REFERENCES products(id),
    quantity INTEGER NOT NULL,
    total DECIMAL(10,2) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO users (username, email, role) VALUES
    ('admin', 'admin@example.com', 'admin'),
    ('ivan', 'ivan@example.com', 'user'),
    ('maria', 'maria@example.com', 'user'),
    ('petr', 'petr@example.com', 'manager'),
    ('anna', 'anna@example.com', 'user'),
    ('dmitry', 'dmitry@example.com', 'user'),
    ('elena', 'elena@example.com', 'manager'),
    ('sergey', 'sergey@example.com', 'user'),
    ('olga', 'olga@example.com', 'user'),
    ('alexey', 'alexey@example.com', 'admin');

INSERT INTO products (name, price, category, stock) VALUES
    ('Ноутбук Lenovo ThinkPad', 89990.00, 'Электроника', 15),
    ('Монитор Dell 27"', 32500.00, 'Электроника', 8),
    ('Клавиатура Logitech MX', 7500.00, 'Периферия', 50),
    ('Мышь Razer DeathAdder', 4200.00, 'Периферия', 35),
    ('Кресло Herman Miller', 128900.00, 'Мебель', 5),
    ('Стол IKEA BEKANT', 24990.00, 'Мебель', 12),
    ('Наушники Sony WH-1000', 27990.00, 'Электроника', 20),
    ('Веб-камера Logitech C920', 8900.00, 'Периферия', 30),
    ('SSD Samsung 1TB', 9500.00, 'Комплектующие', 45),
    ('RAM DDR5 32GB', 12500.00, 'Комплектующие', 25);

INSERT INTO orders (user_id, product_id, quantity, total, status) VALUES
    (2, 1, 1, 89990.00, 'completed'),
    (2, 3, 2, 15000.00, 'completed'),
    (3, 2, 1, 32500.00, 'pending'),
    (4, 4, 3, 12600.00, 'shipped'),
    (5, 5, 1, 128900.00, 'pending'),
    (6, 7, 1, 27990.00, 'completed'),
    (7, 9, 2, 19000.00, 'shipped'),
    (8, 6, 1, 24990.00, 'completed'),
    (3, 10, 4, 50000.00, 'pending'),
    (9, 8, 1, 8900.00, 'completed'),
    (10, 1, 1, 89990.00, 'shipped'),
    (2, 7, 1, 27990.00, 'pending');
