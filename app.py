import os
import uuid
from datetime import datetime
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

import config
from models import db, Product, ProductImage, Discount, Order, OrderItem, AdminUser, Review

app = Flask(__name__)
app.config.from_object(config)

db.init_app(app)

with app.app_context():
    db.create_all()
    from sqlalchemy import text
    with db.engine.connect() as conn:
        for col, definition in [('province', 'VARCHAR(100)'), ('city', 'VARCHAR(100)'), ('shipping_method', 'VARCHAR(50)'), ('shipping_cost', 'INTEGER DEFAULT 0'), ('receipt_image', 'VARCHAR(300)')]:
            try:
                conn.execute(text(f'ALTER TABLE orders ADD COLUMN {col} {definition}'))
                conn.commit()
            except Exception:
                pass

    if not AdminUser.query.first():
        db.session.add(AdminUser(username=config.ADMIN_USERNAME,
                                  password_hash=generate_password_hash(config.ADMIN_PASSWORD)))
        db.session.commit()


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


@app.route('/product/<int:pid>/review', methods=['POST'])
def product_review(pid):
    product = Product.query.get_or_404(pid)
    name = request.form.get('name', '').strip()
    rating = request.form.get('rating', '').strip()
    comment = request.form.get('comment', '').strip()

    if not name or rating not in ('1', '2', '3', '4', '5'):
        flash('لطفاً نام و امتیاز را به‌درستی وارد کنید.', 'error')
    else:
        review = Review(product_id=product.id, name=name, rating=int(rating), comment=comment)
        db.session.add(review)
        db.session.commit()
        flash('نظر شما ثبت شد و پس از بررسی نمایش داده می‌شود.', 'success')

    return redirect(url_for('product_detail', pid=product.id))


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
        session['cart'] = {}
        return redirect(url_for('checkout_payment', oid=order.id))

    products_total = cart_total(cart)
    return render_template('checkout.html', products_total=products_total, cart=cart,
                           shipping_pishtaz=config.SHIPPING_PISHTAZ,
                           shipping_tipax=config.SHIPPING_TIPAX)


@app.route('/checkout/payment/<int:oid>', methods=['GET', 'POST'])
def checkout_payment(oid):
    order = Order.query.get_or_404(oid)

    if request.method == 'POST':
        receipt = save_image(request.files.get('receipt'))
        if receipt:
            order.receipt_image = receipt
            order.status = 'awaiting_confirmation'
            db.session.commit()
            flash('رسید پرداخت با موفقیت ثبت شد. سفارش شما پس از بررسی تایید می‌شود.', 'success')
        else:
            flash('لطفاً یک تصویر معتبر از رسید انتخاب کنید.', 'error')
        return redirect(url_for('checkout_payment', oid=order.id))

    return render_template('payment_info.html', order=order,
                           card_number=config.CARD_NUMBER,
                           card_holder=config.CARD_HOLDER_NAME,
                           card_bank=config.CARD_BANK_NAME)


# ─── admin ────────────────────────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        admin = AdminUser.query.first()
        if (admin and request.form['username'] == admin.username and
                check_password_hash(admin.password_hash, request.form['password'])):
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        flash('نام کاربری یا رمز عبور اشتباه است.', 'error')
    return render_template('admin/login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))


@app.route('/admin/change-password', methods=['GET', 'POST'])
@admin_required
def admin_change_password():
    if request.method == 'POST':
        admin = AdminUser.query.first()
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not check_password_hash(admin.password_hash, current_password):
            flash('رمز عبور فعلی اشتباه است.', 'error')
        elif len(new_password) < 6:
            flash('رمز عبور جدید باید حداقل ۶ کاراکتر باشد.', 'error')
        elif new_password != confirm_password:
            flash('رمز عبور جدید و تکرار آن یکسان نیستند.', 'error')
        else:
            admin.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash('رمز عبور با موفقیت تغییر کرد.', 'success')
            return redirect(url_for('admin_dashboard'))
    return render_template('admin/change_password.html')


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


@app.route('/admin/reviews')
@admin_required
def admin_reviews():
    reviews = Review.query.order_by(Review.is_approved.asc(), Review.created_at.desc()).all()
    return render_template('admin/reviews.html', reviews=reviews)


@app.route('/admin/reviews/<int:rid>/approve', methods=['POST'])
@admin_required
def admin_review_approve(rid):
    review = Review.query.get_or_404(rid)
    review.is_approved = True
    db.session.commit()
    flash('نظر تایید شد.', 'success')
    return redirect(url_for('admin_reviews'))


@app.route('/admin/reviews/<int:rid>/delete', methods=['POST'])
@admin_required
def admin_review_delete(rid):
    review = Review.query.get_or_404(rid)
    db.session.delete(review)
    db.session.commit()
    flash('نظر حذف شد.', 'success')
    return redirect(url_for('admin_reviews'))


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


@app.route('/admin/orders/<int:oid>/delete', methods=['POST'])
@admin_required
def admin_order_delete(oid):
    order = Order.query.get_or_404(oid)
    db.session.delete(order)
    db.session.commit()
    flash('سفارش حذف شد.', 'success')
    return redirect(url_for('admin_orders'))


@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}


if __name__ == '__main__':
    app.run(debug=True)
