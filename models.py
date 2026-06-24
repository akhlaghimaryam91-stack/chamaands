from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Integer, nullable=False)
    stock = db.Column(db.Integer, default=0)
    is_made_to_order = db.Column(db.Boolean, default=False)
    delivery_days = db.Column(db.Integer, default=7)
    image = db.Column(db.String(300))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    discounts = db.relationship('Discount', backref='product', lazy=True)
    images = db.relationship('ProductImage', backref='product', lazy=True,
                             cascade='all, delete-orphan')

    def effective_price(self):
        best = self.best_discount()
        if best:
            if best.discount_type == 'percent':
                return int(self.price * (1 - best.value / 100))
            else:
                return max(0, self.price - best.value)
        return self.price

    def best_discount(self):
        now = datetime.utcnow()
        active = [d for d in self.discounts
                  if d.is_active and (d.expires_at is None or d.expires_at > now)
                  and d.min_quantity <= 1]
        if not active:
            return None
        return max(active, key=lambda d: d.value if d.discount_type == 'percent'
                   else d.value / self.price * 100)

    def bulk_discount(self, qty):
        now = datetime.utcnow()
        bulk = [d for d in self.discounts
                if d.is_active and (d.expires_at is None or d.expires_at > now)
                and d.min_quantity > 1 and qty >= d.min_quantity]
        if not bulk:
            return None
        return max(bulk, key=lambda d: d.min_quantity)

    def is_available(self):
        return self.is_made_to_order or self.stock > 0


class ProductImage(db.Model):
    __tablename__ = 'product_images'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    filename = db.Column(db.String(300), nullable=False)
    sort_order = db.Column(db.Integer, default=0)


class Discount(db.Model):
    __tablename__ = 'discounts'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    title = db.Column(db.String(200))
    discount_type = db.Column(db.String(10), default='percent')
    value = db.Column(db.Integer, nullable=False)
    min_quantity = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    address = db.Column(db.Text, nullable=False)
    postal_code = db.Column(db.String(20))
    province = db.Column(db.String(100))
    city = db.Column(db.String(100))
    shipping_method = db.Column(db.String(50))
    shipping_cost = db.Column(db.Integer, default=0)
    total_amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='pending')
    authority = db.Column(db.String(200))
    ref_id = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('OrderItem', backref='order', lazy=True)


class OrderItem(db.Model):
    __tablename__ = 'order_items'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    product_name = db.Column(db.String(200))
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Integer, nullable=False)
    product = db.relationship('Product')
