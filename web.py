import os
import psycopg2
import requests
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

# تحميل متغيرات البيئة من ملف .env
load_dotenv()

app = Flask(__name__)

# دالة للاتصال بقاعدة البيانات PostgreSQL
def get_db_connection():
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    return conn

# 1. الصفحة الرئيسية: عرض الأخبار
@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor()
    # جلب آخر 20 خبراً من القاعدة
    cur.execute('SELECT title, source, ai_summary, sentiment, url, published_at FROM posted_news ORDER BY published_at DESC LIMIT 20;')
    news_list = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('index.html', news=news_list)

# 2. API لجلب الأسعار الحية للموقع
@app.route('/api/prices')
def get_prices():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,binancecoin,solana,ripple&vs_currencies=usd&include_24hr_change=true"
        response = requests.get(url).json()
        return jsonify(response)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 3. إدارة النظام (Admin): إنشاء الجداول
@app.route('/admin/init')
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # إنشاء الجداول إذا لم تكن موجودة
    cur.execute('''
        CREATE TABLE IF NOT EXISTS posted_news (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            source VARCHAR(255),
            ai_summary TEXT,
            sentiment VARCHAR(20),
            url TEXT UNIQUE,
            published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS telegram_log (
            id SERIAL PRIMARY KEY,
            title_hash TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    conn.commit()
    cur.close()
    conn.close()
    return "✅ Database initialized successfully!"

# 4. إدارة النظام (Admin): مسح سجل تلغرام
@app.route('/admin/clear')
def clear_log():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM telegram_log;')
    conn.commit()
    cur.close()
    conn.close()
    return "🗑️ Telegram log cleared!"

if __name__ == '__main__':
    # تشغيل التطبيق (سيستخدم Railway المنفذ المخصص له تلقائياً)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
