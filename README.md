# Elektron Jurnal Tizimi

Flask, SQLite va SQLAlchemy asosida yozilgan ko'p rolli elektron jurnal tizimi.

## Texnologiyalar

- Flask
- Flask-Login
- Flask-SQLAlchemy
- SQLite
- HTML, CSS, JavaScript

## Imkoniyatlar

- Admin, o'qituvchi va talaba rollari
- Login/logout va role-based access
- Foydalanuvchi CRUD
- Profil rasmini serverga yuklash
- Guruh va fan boshqaruvi
- Davomat kiritish
- Baho kiritish
- Topshiriq yaratish
- Hisobotlar
- Seed demo ma'lumotlar

## Ishga tushirish

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

## Demo loginlar

- Admin: `admin` / `admin123`
- Teacher: `teacher` / `teacher123`
- Student: `student` / `student123`

## Struktura

```text
app.py
config.py
models.py
requirements.txt
static/
templates/
uploads/
```
# jurnal
