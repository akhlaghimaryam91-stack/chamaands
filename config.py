import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SECRET_KEY = os.getenv('SECRET_KEY', 'chamaan-secret-key-change-in-production')
SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'chamaan.db')
SQLALCHEMY_TRACK_MODIFICATIONS = False

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB

# پرداخت کارت به کارت
CARD_NUMBER = os.getenv('CARD_NUMBER', '6221-0610-6775-4038')
CARD_HOLDER_NAME = os.getenv('CARD_HOLDER_NAME', 'ندا پیله‌چیان لنگرودی')
CARD_BANK_NAME = os.getenv('CARD_BANK_NAME', 'بانک پارسیان')

# Shipping costs (تومان) — برای تغییر قیمت فقط همین دو خط رو ویرایش کن
SHIPPING_PISHTAZ = 50000
SHIPPING_TIPAX = 100000

# Admin
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'chamaan1234')
