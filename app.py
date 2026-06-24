import os
import uuid
import requests
from datetime import datetime
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, abort)
from werkzeug.utils import secure_filename
from functools import wraps

import config
from models import db, Product, ProductImage, Discount, Order, OrderItem

app = Flask(__name__)
app.config.from_object(config)

db.init_app(app)

with app.app_context():
    db.create_all()
    from sqlalchemy import text
    with db.engine.connect() as conn:
        for col, definition in [('province', 'VARCHAR(100)'), ('city', 'VARCHAR(100)'), ('shipping_method', 'VARCHAR(50)'), ('shipping_cost', 'INTEGER DEFAULT 0')]:
            try:
                conn.execute(text(f'ALTER TABLE orders ADD COLUMN {col} {definition}'))
                conn.commit()
            except Exception:
                pass


@app.before_request
def force_https():
    if request.headers.get('X-Forwarded-Proto') == 'http':
        return redirect(request.url.replace('http://', 'https://', 1), code=301)


# ─── helpers ────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in config.ALLOWED_EXTENSIONS


def save_image(file):
    if file and file.filename and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(config.UPLOAD_FOLDER, filename))
        return filename
    return None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


def format_price(amount):
    return f"{amount:,}"

app.jinja_env.filters['price'] = format_price


# ─── cart helpers ────────────────────────────────────────────────────────────

def get_cart():
    return session.get('cart', {})


def cart_total(cart):
    total = 0
    for pid, item in cart.items():
        product = Product.query.get(int(pid))
        if product:
            disc = product.bulk_discount(item['qty'])
            if disc:
                unit = int(product.price * (1 - disc.value / 100)) \
                    if disc.discount_type == 'percent' \
                    else max(0, product.price - disc.value)
            else:
                unit = product.effective_price()
            total += unit * item['qty']
    return total


# ─── public routes ───────────────────────────────────────────────────────────

@app.route('/')
def index():
    products = Product.query.filter_by(is_active=True).all()
    return render_template('index.html', products=products)


@app.route('/product/<int:pid>')
def product_detail(pid):
    product = Product.query.get_or_404(pid)
    return render_template('product.html', product=product)


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/contact')
def contact():
    return render_template('contact.html')


# ─── cart ────────────────────────────────────────────────────────────────────

@app.route('/cart')
def cart():
    cart = get_cart()
    items = []
    for pid, item in cart.items():
        product = Product.query.get(int(pid))
        if product:
            disc = product.bulk_discount(item['qty'])
            if disc:
                unit = int(product.price * (1 - disc.value / 100)) \
                    if disc.discount_type == 'percent' \
                    else max(0, product.price - disc.value)
                bulk_label = f"{disc.value}{'٪' if disc.discount_type == 'percent' else ' تومان'} تخفیف عمده"
            else:
                unit = product.effective_price()
                bulk_label = None
            items.append({
                'product': product,
                'qty': item['qty'],
                'unit_price': unit,
                'subtotal': unit * item['qty'],
                'bulk_label': bulk_label,
            })
    total = sum(i['subtotal'] for i in items)
    return render_template('cart.html', items=items, total=total)


@app.route('/cart/add/<int:pid>', methods=['POST'])
def cart_add(pid):
    product = Product.query.get_or_404(pid)
    qty = int(request.form.get('qty', 1))
    cart = get_cart()
    key = str(pid)
    if product.is_made_to_order:
        cart[key] = {'qty': cart[key]['qty'] + qty if key in cart else qty}
    else:
        if key in cart:
            cart[key]['qty'] = min(cart[key]['qty'] + qty, product.stock)
        else:
            cart[key] = {'qty': min(qty, product.stock)}
    session['cart'] = cart
    flash('محصول به سبد خرید اضافه شد.', 'success')
    return redirect(url_for('cart'))


@app.route('/cart/update', methods=['POST'])
def cart_update():
    cart = get_cart()
    pid = request.form.get('pid')
    qty = int(request.form.get('qty', 1))
    if pid in cart:
        if qty <= 0:
            del cart[pid]
        else:
            product = Product.query.get(int(pid))
            if product and not product.is_made_to_order:
                cart[pid]['qty'] = min(qty, product.stock)
            else:
                cart[pid]['qty'] = qty
    session['cart'] = cart
    return redirect(url_for('cart'))


@app.route('/cart/remove/<pid>')
def cart_remove(pid):
    cart = get_cart()
    cart.pop(pid, None)
    session['cart'] = cart
    return redirect(url_for('cart'))


# ─── checkout & payment ───────────────────────────────────────────────────────

@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    cart = get_cart()
    if not cart:
        return redirect(url_for('cart'))

    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        phone = request.form['phone'].strip()
        province = request.form.get('province', '').strip()
        city = request.form.get('city', '').strip()
        address = request.form['address'].strip()
        postal_code = request.form.get('postal_code', '').strip()
        shipping_method = request.form.get('shipping_method', 'pishtaz')
        shipping_cost = config.SHIPPING_TIPAX if shipping_method == 'tipax' else config.SHIPPING_PISHTAZ
        products_total = cart_total(cart)
        total = products_total + shipping_cost

        order = Order(full_name=full_name, phone=phone, province=province, city=city,
                      address=address, postal_code=postal_code, shipping_method=shipping_method,
                      shipping_cost=shipping_cost, total_amount=total)
        db.session.add(order)
        db.session.flush()

        for pid, item in cart.items():
            product = Product.query.get(int(pid))
            if product:
                disc = product.bulk_discount(item['qty'])
                if disc:
                    unit = int(product.price * (1 - disc.value / 100)) \
                        if disc.discount_type == 'percent' \
                        else max(0, product.price - disc.value)
                else:
                    unit = product.effective_price()
                oi = OrderItem(order_id=order.id, product_id=product.id,
                               product_name=product.name,
                               quantity=item['qty'], unit_price=unit)
                db.session.add(oi)
                if not product.is_made_to_order:
                    product.stock = max(0, product.stock - item['qty'])

        db.session.commit()

        data = {
            'merchant_id': config.ZAINPAL_MERCHANT,
            'amount': total * 10,
            'callback_url': config.ZAINPAL_CALLBACK,
            'description': f'سفارش شماره {order.id} - چمان',
            'metadata': {'mobile': phone, 'email': ''}
        }
        try:
            resp = requests.post(
                'https://api.zarinpal.com/pg/v4/payment/request.json',
                json=data, timeout=10
            )
            result = resp.json()
            if result.get('data', {}).get('code') == 100:
                authority = result['data']['authority']
                order.authority = authority
                db.session.commit()
                session['cart'] = {}
                return redirect(f"https://www.zarinpal.com/pg/StartPay/{authority}")
            else:
                flash('خطا در اتصال به درگاه پرداخت.', 'error')
                db.session.delete(order)
                db.session.commit()
        except Exception:
            flash('خطا در اتصال به درگاه پرداخت.', 'error')
            db.session.delete(order)
            db.session.commit()

        return redirect(url_for('checkout'))

    products_total = cart_total(cart)
    return render_template('checkout.html', products_total=products_total, cart=cart,
                           shipping_pishtaz=config.SHIPPING_PISHTAZ,
                           shipping_tipax=config.SHIPPING_TIPAX)


@app.route('/payment/verify')
def payment_verify():
    authority = request.args.get('Authority')
    status = request.args.get('Status')
    order = Order.query.filter_by(authority=authority).first()
    if not order:
        abort(404)

    if status == 'OK':
        data = {
            'merchant_id': config.ZAINPAL_MERCHANT,
            'amount': order.total_amount * 10,
            'authority': authority
        }
        try:
            resp = requests.post(
                'https://api.zarinpal.com/pg/v4/payment/verify.json',
                json=data, timeout=10
            )
            result = resp.json()
            if result.get('data', {}).get('code') in (100, 101):
                ref_id = result['data']['ref_id']
                order.status = 'paid'
                order.ref_id = str(ref_id)
                db.session.commit()
                return render_template('payment_result.html',
                                       success=True, order=order, ref_id=ref_id)
        except Exception:
            pass

    order.status = 'failed'
    db.session.commit()
    return render_template('payment_result.html', success=False, order=order)


# ─── admin ────────────────────────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if (request.form['username'] == config.ADMIN_USERNAME and
                request.form['password'] == config.ADMIN_PASSWORD):
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        flash('نام کاربری یا رمز عبور اشتباه است.', 'error')
    return render_template('admin/login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))


@app.route('/admin')
@admin_required
def admin_dashboard():
    total_orders = Order.query.count()
    paid_orders = Order.query.filter_by(status='paid').count()
    total_revenue = db.session.query(
        db.func.sum(Order.total_amount)
    ).filter_by(status='paid').scalar() or 0
    products_count = Product.query.count()
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(10).all()
    return render_template('admin/dashboard.html',
                           total_orders=total_orders, paid_orders=paid_orders,
                           total_revenue=total_revenue,
                           products_count=products_count,
                           recent_orders=recent_orders)


@app.route('/admin/products')
@admin_required
def admin_products():
    products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('admin/products.html', products=products)


@app.route('/admin/products/new', methods=['GET', 'POST'])
@admin_required
def admin_product_new():
    if request.method == 'POST':
        stock_type = request.form.get('stock_type', 'stock')
        is_made_to_order = (stock_type == 'order')
        image_file = save_image(request.files.get('image'))
        product = Product(
            name=request.form['name'],
            description=request.form.get('description', ''),
            price=int(request.form['price']),
            stock=int(request.form.get('stock', 0)) if not is_made_to_order else 0,
            is_made_to_order=is_made_to_order,
            delivery_days=int(request.form.get('delivery_days', 7)),
            image=image_file,
            is_active='is_active' in request.form
        )
        db.session.add(product)
        db.session.flush()

        for f in request.files.getlist('gallery_images'):
            fname = save_image(f)
            if fname:
                db.session.add(ProductImage(product_id=product.id, filename=fname))

        db.session.commit()
        flash('محصول با موفقیت اضافه شد.', 'success')
        return redirect(url_for('admin_products'))
    return render_template('admin/product_form.html', product=None)


@app.route('/admin/products/edit/<int:pid>', methods=['GET', 'POST'])
@admin_required
def admin_product_edit(pid):
    product = Product.query.get_or_404(pid)
    if request.method == 'POST':
        stock_type = request.form.get('stock_type', 'stock')
        product.is_made_to_order = (stock_type == 'order')
        product.name = request.form['name']
        product.description = request.form.get('description', '')
        product.price = int(request.form['price'])
        product.stock = int(request.form.get('stock', 0)) if not product.is_made_to_order else 0
        product.delivery_days = int(request.form.get('delivery_days', 7))
        product.is_active = 'is_active' in request.form
        new_image = save_image(request.files.get('image'))
        if new_image:
            product.image = new_image
        for f in request.files.getlist('gallery_images'):
            fname = save_image(f)
            if fname:
                db.session.add(ProductImage(product_id=product.id, filename=fname))
        db.session.commit()
        flash('محصول ویرایش شد.', 'success')
        return redirect(url_for('admin_products'))
    return render_template('admin/product_form.html', product=product)


@app.route('/admin/products/delete/<int:pid>', methods=['POST'])
@admin_required
def admin_product_delete(pid):
    product = Product.query.get_or_404(pid)
    db.session.delete(product)
    db.session.commit()
    flash('محصول حذف شد.', 'success')
    return redirect(url_for('admin_products'))


@app.route('/admin/images/delete/<int:iid>')
@admin_required
def admin_image_delete(iid):
    img = ProductImage.query.get_or_404(iid)
    pid = img.product_id
    db.session.delete(img)
    db.session.commit()
    flash('تصویر حذف شد.', 'success')
    return redirect(url_for('admin_product_edit', pid=pid))


@app.route('/admin/discounts')
@admin_required
def admin_discounts():
    discounts = Discount.query.order_by(Discount.created_at.desc()).all()
    products = Product.query.filter_by(is_active=True).all()
    return render_template('admin/discounts.html', discounts=discounts, products=products)


@app.route('/admin/discounts/new', methods=['POST'])
@admin_required
def admin_discount_new():
    expires_raw = request.form.get('expires_at')
    expires = datetime.strptime(expires_raw, '%Y-%m-%d') if expires_raw else None
    disc = Discount(
        product_id=int(request.form['product_id']),
        title=request.form['title'],
        discount_type=request.form['discount_type'],
        value=int(request.form['value']),
        min_quantity=int(request.form.get('min_quantity', 1)),
        is_active=True,
        expires_at=expires
    )
    db.session.add(disc)
    db.session.commit()
    flash('تخفیف ثبت شد.', 'success')
    return redirect(url_for('admin_discounts'))


@app.route('/admin/discounts/toggle/<int:did>')
@admin_required
def admin_discount_toggle(did):
    disc = Discount.query.get_or_404(did)
    disc.is_active = not disc.is_active
    db.session.commit()
    return redirect(url_for('admin_discounts'))


@app.route('/admin/discounts/delete/<int:did>', methods=['POST'])
@admin_required
def admin_discount_delete(did):
    disc = Discount.query.get_or_404(did)
    db.session.delete(disc)
    db.session.commit()
    flash('تخفیف حذف شد.', 'success')
    return redirect(url_for('admin_discounts'))


@app.route('/admin/orders')
@admin_required
def admin_orders():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template('admin/orders.html', orders=orders)


@app.route('/admin/orders/<int:oid>/status', methods=['POST'])
@admin_required
def admin_order_status(oid):
    order = Order.query.get_or_404(oid)
    order.status = request.form['status']
    db.session.commit()
    flash('وضعیت سفارش به‌روز شد.', 'success')
    return redirect(url_for('admin_orders'))


@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}


if __name__ == '__main__':
    app.run(debug=True)
