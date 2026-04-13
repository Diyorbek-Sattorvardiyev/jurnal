from __future__ import annotations

import uuid
import csv
from datetime import date, datetime, timedelta
from functools import wraps
from io import StringIO
from pathlib import Path

from flask import (
    Response,
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from sqlalchemy import and_, or_
from sqlalchemy import inspect, text
from werkzeug.utils import secure_filename

from config import Config
from models import (
    ActivityLog,
    Assignment,
    AssignmentSubmission,
    Attendance,
    ChatMessage,
    Grade,
    Group,
    Notification,
    StudentGroup,
    Subject,
    SubjectGroup,
    User,
    db,
)


app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Tizimdan foydalanish uchun avval login qiling."
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in roles:
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_IMAGE_EXTENSIONS"]


def allowed_document(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_DOCUMENT_EXTENSIONS"]


def save_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_image(file_storage.filename):
        flash("Faqat rasm fayllari yuklash mumkin: png, jpg, jpeg, gif, webp.", "error")
        return None
    filename = secure_filename(file_storage.filename)
    extension = filename.rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{extension}"
    save_path = Path(app.config["UPLOAD_FOLDER"]) / unique_name
    file_storage.save(save_path)
    return unique_name


def save_document(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_document(file_storage.filename):
        flash("Faqat pdf, doc, docx, xls yoki xlsx fayllar yuklash mumkin.", "error")
        return None
    filename = secure_filename(file_storage.filename)
    extension = filename.rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{extension}"
    save_path = Path(app.config["DOCUMENT_UPLOAD_FOLDER"]) / unique_name
    file_storage.save(save_path)
    return unique_name


def avatar_url(user: User) -> str:
    if user.image_path:
        return url_for("static", filename=f"uploads/{user.image_path}")
    return url_for("static", filename="uploads/default-avatar.svg")


def document_url(filename: str | None) -> str | None:
    if not filename:
        return None
    return url_for("static", filename=f"documents/{filename}")


def log_activity(message: str, category: str = "info", user_id: int | None = None):
    activity = ActivityLog(user_id=user_id or (current_user.id if current_user.is_authenticated else None), message=message, category=category)
    db.session.add(activity)


def create_notification(user_id: int, title: str, message: str, category: str = "info", target_url: str | None = None):
    db.session.add(Notification(user_id=user_id, title=title, message=message, category=category, target_url=target_url))


def notify_group_students(group_id: int, title: str, message: str, category: str = "info", target_url: str | None = None):
    students = (
        User.query.join(StudentGroup)
        .filter(User.role == "student", StudentGroup.group_id == group_id)
        .all()
    )
    for student in students:
        create_notification(student.id, title, message, category, target_url=target_url)


def get_teacher_groups(teacher: User):
    group_ids = sorted({link.group_id for subject in teacher.taught_subjects for link in subject.group_links})
    if not group_ids:
        return []
    return Group.query.filter(Group.id.in_(group_ids)).order_by(Group.name).all()


def get_visible_subjects_for_teacher(teacher: User):
    return teacher.taught_subjects.order_by(Subject.name).all()


def can_manage_student(target_user: User | None = None, group_id: int | None = None) -> bool:
    if current_user.role == "admin":
        return True
    if current_user.role != "teacher":
        return False
    teacher_group_ids = {group.id for group in get_teacher_groups(current_user)}
    if target_user is not None:
        return target_user.role == "student" and target_user.group and target_user.group.id in teacher_group_ids
    return group_id in teacher_group_ids if group_id else False


def chat_groups_for_user():
    if current_user.role == "admin":
        return Group.query.order_by(Group.name).all()
    if current_user.role == "teacher":
        return get_teacher_groups(current_user)
    return [current_user.group] if current_user.group else []


def private_chat_users():
    if current_user.role == "admin":
        return User.query.filter(User.id != current_user.id).order_by(User.full_name).all()
    if current_user.role == "teacher":
        group_ids = [group.id for group in get_teacher_groups(current_user)]
        return (
            User.query.join(StudentGroup)
            .filter(User.role == "student", StudentGroup.group_id.in_(group_ids) if group_ids else False)
            .order_by(User.full_name)
            .all()
        )
    teacher_ids = sorted({subject.teacher_id for subject in current_user.group.subjects if subject.teacher_id} if current_user.group else set())
    return User.query.filter(User.id.in_(teacher_ids) if teacher_ids else False).order_by(User.full_name).all()


def resolve_private_group_id(other_user: User) -> int | None:
    if current_user.group:
        return current_user.group.id
    if other_user.group:
        return other_user.group.id
    teacher_groups = get_teacher_groups(current_user) if current_user.role == "teacher" else []
    if teacher_groups:
        return teacher_groups[0].id
    if other_user.role == "teacher":
        other_groups = get_teacher_groups(other_user)
        if other_groups:
            return other_groups[0].id
    fallback_group = Group.query.order_by(Group.id).first()
    return fallback_group.id if fallback_group else None


def seed_database():
    if User.query.first():
        return

    admin = User(full_name="Boshqaruvchi Admin", username="admin", role="admin", phone="+998901112233", email="admin@jurnal.uz")
    admin.set_password("admin123")
    teacher = User(full_name="Dilnoza Karimova", username="teacher", role="teacher", phone="+998901234567", email="teacher@jurnal.uz")
    teacher.set_password("teacher123")
    student = User(full_name="Ali Valiyev", username="student", role="student", phone="+998907778899", email="student@jurnal.uz")
    student.set_password("student123")
    student2 = User(full_name="Madina Xasanova", username="student2", role="student", phone="+998909998877", email="madina@jurnal.uz")
    student2.set_password("student123")

    group_a = Group(name="11-A")
    group_b = Group(name="CS-101")

    math = Subject(name="Matematika", teacher=teacher)
    physics = Subject(name="Fizika", teacher=teacher)

    db.session.add_all([admin, teacher, student, student2, group_a, group_b, math, physics])
    db.session.flush()

    db.session.add_all(
        [
            StudentGroup(student=student, group=group_a),
            StudentGroup(student=student2, group=group_a),
            SubjectGroup(subject=math, group=group_a),
            SubjectGroup(subject=physics, group=group_a),
        ]
    )

    today = date.today()
    db.session.add_all(
        [
            Grade(student=student, subject=math, teacher=teacher, score=4.8, comment="Nazorat ishi a'lo topshirildi.", date=today - timedelta(days=2)),
            Grade(student=student, subject=physics, teacher=teacher, score=4.4, comment="Amaliy mashg'ulotda faol qatnashdi.", date=today - timedelta(days=5)),
            Grade(student=student2, subject=math, teacher=teacher, score=3.9, comment="Yaxshi ishladi.", date=today - timedelta(days=4)),
            Attendance(student=student, group=group_a, subject=math, date=today - timedelta(days=1), status="present"),
            Attendance(student=student, group=group_a, subject=physics, date=today - timedelta(days=2), status="late"),
            Attendance(student=student2, group=group_a, subject=math, date=today - timedelta(days=1), status="absent"),
            Assignment(
                title="Kvadrat tenglamalar",
                description="10 ta misolni yeching va daftar shaklida topshiring.",
                deadline=today + timedelta(days=3),
                subject=math,
                group=group_a,
                teacher=teacher,
            ),
            Assignment(
                title="Fizika laboratoriya hisobot",
                description="Tajriba natijalarini jadval va xulosa bilan yuboring.",
                deadline=today + timedelta(days=5),
                subject=physics,
                group=group_a,
                teacher=teacher,
            ),
        ]
    )

    db.session.add_all(
        [
            ActivityLog(user=admin, message="Boshlang'ich administrator hisobi yaratildi.", category="success"),
            ActivityLog(user=teacher, message="O'qituvchi uchun Matematika va Fizika fanlari biriktirildi.", category="info"),
            ActivityLog(user=student, message="Talaba uchun namunaviy baholar va davomat kiritildi.", category="info"),
        ]
    )
    db.session.flush()
    db.session.add_all(
        [
            Notification(user=student, title="Yangi topshiriq", message="Matematika fanidan yangi topshiriq berildi.", category="info"),
            Notification(user=student2, title="Xush kelibsiz", message="Sizning shaxsiy kabinetingiz tayyor.", category="success"),
            ChatMessage(sender=teacher, group=group_a, message="Assalomu alaykum, guruh. Bugun kvadrat tenglamalarni takrorlaymiz."),
            ChatMessage(sender=student, group=group_a, message="Assalomu alaykum ustoz, topshiriq muddatini yana bir marta yuborasizmi?"),
        ]
    )
    db.session.commit()


@app.context_processor
def inject_globals():
    nav = []
    role_labels = {"admin": "Administrator", "teacher": "O'qituvchi", "student": "Talaba"}
    status_labels = {"present": "Bor", "absent": "Yo'q", "late": "Kechikdi"}
    status_options = [("present", "Bor"), ("absent", "Yo'q"), ("late", "Kechikdi")]
    unread_notifications = 0
    if current_user.is_authenticated:
        unread_notifications = current_user.notifications.filter_by(is_read=False).count()
        if current_user.role == "admin":
            nav = [
                ("admin_dashboard", "Bosh sahifa"),
                ("user_list", "Foydalanuvchilar"),
                ("group_list", "Guruhlar"),
                ("subject_list", "Fanlar"),
                ("attendance_page", "Davomat"),
                ("grade_page", "Baholar"),
                ("assignment_page", "Topshiriqlar"),
                ("calendar_page", "Kalendar"),
                ("notification_page", "Bildirishnomalar"),
                ("chat_page", "Chat"),
                ("reports_page", "Hisobotlar"),
                ("profile_page", "Profil"),
            ]
        elif current_user.role == "teacher":
            nav = [
                ("teacher_dashboard", "Bosh sahifa"),
                ("student_list", "Talabalar"),
                ("attendance_page", "Davomat"),
                ("grade_page", "Baholar"),
                ("assignment_page", "Topshiriqlar"),
                ("calendar_page", "Kalendar"),
                ("notification_page", "Bildirishnomalar"),
                ("chat_page", "Chat"),
                ("profile_page", "Profil"),
            ]
        else:
            nav = [
                ("student_dashboard", "Bosh sahifa"),
                ("assignment_page", "Topshiriqlar"),
                ("calendar_page", "Kalendar"),
                ("notification_page", "Bildirishnomalar"),
                ("chat_page", "Chat"),
                ("profile_page", "Profil"),
            ]
    return {
        "nav_items": nav,
        "avatar_url": avatar_url,
        "today": date.today(),
        "role_labels": role_labels,
        "status_labels": status_labels,
        "status_options": status_options,
        "unread_notifications": unread_notifications,
        "document_url": document_url,
    }


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="Sizda ushbu sahifaga kirish huquqi yo'q."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="Sahifa topilmadi."), 404


@app.errorhandler(413)
def payload_too_large(_error):
    message = "Yuklanayotgan fayl juda katta. Maksimal ruxsat etilgan hajm: 25 MB."
    if current_user.is_authenticated:
        flash(message, "error")
        return redirect(request.referrer or url_for("dashboard_router"))
    return render_template("error.html", code=413, message=message), 413


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard_router"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard_router"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            db.session.add(ActivityLog(user=user, message="Tizimga muvaffaqiyatli kirdi.", category="success"))
            db.session.commit()
            flash("Tizimga muvaffaqiyatli kirdingiz.", "success")
            return redirect(url_for("dashboard_router"))
        flash("Login yoki parol noto'g'ri.", "error")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    db.session.add(ActivityLog(user=current_user, message="Tizimdan chiqdi.", category="info"))
    db.session.commit()
    logout_user()
    flash("Tizimdan chiqdingiz.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard_router():
    mapping = {
        "admin": "admin_dashboard",
        "teacher": "teacher_dashboard",
        "student": "student_dashboard",
    }
    return redirect(url_for(mapping[current_user.role]))


@app.route("/admin/dashboard")
@login_required
@role_required("admin")
def admin_dashboard():
    total_students = User.query.filter_by(role="student").count()
    total_teachers = User.query.filter_by(role="teacher").count()
    total_groups = Group.query.count()
    total_subjects = Subject.query.count()
    total_attendance = Attendance.query.count()
    today_attendance = Attendance.query.filter_by(date=date.today()).count()
    present_count = Attendance.query.filter_by(status="present").count()
    attendance_rate = round((present_count / total_attendance) * 100, 1) if total_attendance else 0
    overdue_assignments = Assignment.query.filter(Assignment.deadline < date.today()).count()
    unread_notifications_total = Notification.query.filter_by(is_read=False).count()
    active_teachers = (
        User.query.filter_by(role="teacher")
        .join(ActivityLog, ActivityLog.user_id == User.id)
        .filter(ActivityLog.created_at >= datetime.utcnow() - timedelta(days=30))
        .distinct()
        .count()
    )
    activities = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(8).all()
    recent_notifications = Notification.query.order_by(Notification.created_at.desc()).limit(6).all()
    recent_chat = ChatMessage.query.order_by(ChatMessage.created_at.desc()).limit(6).all()

    return render_template(
        "admin_dashboard.html",
        total_students=total_students,
        total_teachers=total_teachers,
        total_groups=total_groups,
        total_subjects=total_subjects,
        today_attendance=today_attendance,
        overdue_assignments=overdue_assignments,
        unread_notifications_total=unread_notifications_total,
        active_teachers=active_teachers,
        attendance_rate=attendance_rate,
        activities=activities,
        recent_notifications=recent_notifications,
        recent_chat=recent_chat,
    )


@app.route("/teacher/dashboard")
@login_required
@role_required("teacher")
def teacher_dashboard():
    subjects = get_visible_subjects_for_teacher(current_user)
    groups = get_teacher_groups(current_user)
    assignments = Assignment.query.filter_by(teacher_id=current_user.id).order_by(Assignment.deadline.asc()).limit(5).all()
    recent_grades = Grade.query.filter_by(teacher_id=current_user.id).order_by(Grade.date.desc()).limit(5).all()
    pending_submissions = (
        AssignmentSubmission.query.join(Assignment, AssignmentSubmission.assignment_id == Assignment.id)
        .filter(Assignment.teacher_id == current_user.id, AssignmentSubmission.score.is_(None))
        .count()
    )
    today_lessons = len(subjects)
    overdue_tasks = Assignment.query.filter(Assignment.teacher_id == current_user.id, Assignment.deadline < date.today()).count()
    notifications = current_user.notifications.order_by(Notification.created_at.desc()).limit(5).all()
    recent_chat = (
        ChatMessage.query.filter(ChatMessage.group_id.in_([group.id for group in groups]) if groups else False)
        .order_by(ChatMessage.created_at.desc())
        .limit(5)
        .all()
    )
    return render_template(
        "teacher_dashboard.html",
        subjects=subjects,
        groups=groups,
        assignments=assignments,
        recent_grades=recent_grades,
        notifications=notifications,
        recent_chat=recent_chat,
        pending_submissions=pending_submissions,
        today_lessons=today_lessons,
        overdue_tasks=overdue_tasks,
    )


@app.route("/student/dashboard")
@login_required
@role_required("student")
def student_dashboard():
    grades = Grade.query.filter_by(student_id=current_user.id).order_by(Grade.date.desc()).limit(6).all()
    attendance = Attendance.query.filter_by(student_id=current_user.id).order_by(Attendance.date.desc()).limit(8).all()
    assignments = []
    if current_user.group:
        assignments = Assignment.query.filter_by(group_id=current_user.group.id).order_by(Assignment.deadline.asc()).limit(6).all()
    attendance_total = current_user.attendance_records.count()
    present_total = current_user.attendance_records.filter_by(status="present").count()
    attendance_rate = round((present_total / attendance_total) * 100, 1) if attendance_total else 0
    grade_avg = round(sum(item.score for item in current_user.grade_records) / current_user.grade_records.count(), 2) if current_user.grade_records.count() else 0
    notifications = current_user.notifications.order_by(Notification.created_at.desc()).limit(5).all()
    latest_feedback = (
        AssignmentSubmission.query.filter_by(student_id=current_user.id)
        .filter(AssignmentSubmission.feedback.isnot(None))
        .order_by(AssignmentSubmission.graded_at.desc())
        .first()
    )
    nearest_deadline = assignments[0] if assignments else None
    group_chat = (
        ChatMessage.query.filter_by(group_id=current_user.group.id).order_by(ChatMessage.created_at.desc()).limit(5).all()
        if current_user.group
        else []
    )
    return render_template(
        "student_dashboard.html",
        grades=grades,
        attendance=attendance,
        assignments=assignments,
        attendance_rate=attendance_rate,
        grade_avg=grade_avg,
        notifications=notifications,
        group_chat=group_chat,
        latest_feedback=latest_feedback,
        nearest_deadline=nearest_deadline,
    )


@app.route("/users")
@login_required
@role_required("admin")
def user_list():
    query = User.query
    search = request.args.get("search", "").strip()
    role = request.args.get("role", "").strip()
    group_id = request.args.get("group_id", "").strip()

    if search:
        query = query.filter(
            or_(
                User.full_name.ilike(f"%{search}%"),
                User.username.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
            )
        )
    if role:
        query = query.filter_by(role=role)
    if group_id:
        query = query.outerjoin(StudentGroup).filter(StudentGroup.group_id == int(group_id))

    users = query.order_by(User.created_at.desc()).all()
    groups = Group.query.order_by(Group.name).all()
    return render_template("users/list.html", users=users, groups=groups, selected_role=role, selected_group=group_id, search=search)


@app.route("/students")
@login_required
@role_required("admin", "teacher")
def student_list():
    query = User.query.filter_by(role="student")
    search = request.args.get("search", "").strip()
    group_id = request.args.get("group_id", "").strip()

    if current_user.role == "teacher":
        group_ids = [group.id for group in get_teacher_groups(current_user)]
        query = query.join(StudentGroup).filter(StudentGroup.group_id.in_(group_ids) if group_ids else False)

    if search:
        query = query.filter(or_(User.full_name.ilike(f"%{search}%"), User.username.ilike(f"%{search}%")))
    if group_id:
        query = query.join(StudentGroup).filter(StudentGroup.group_id == int(group_id))

    students = query.order_by(User.full_name).all()
    groups = Group.query.order_by(Group.name).all()
    return render_template("users/students.html", students=students, groups=groups, selected_group=group_id, search=search)


def upsert_student_group(user: User, group_id: str | None):
    if user.role != "student":
        if user.student_group:
            db.session.delete(user.student_group)
        return
    if not group_id:
        return
    group = db.session.get(Group, int(group_id))
    if not group:
        return
    if user.student_group:
        user.student_group.group = group
    else:
        db.session.add(StudentGroup(student=user, group=group))


@app.route("/users/add", methods=["GET", "POST"])
@login_required
@role_required("admin", "teacher")
def add_user():
    groups = Group.query.order_by(Group.name).all() if current_user.role == "admin" else get_teacher_groups(current_user)
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if User.query.filter_by(username=username).first():
            flash("Bu username allaqachon mavjud.", "error")
            return render_template("users/form.html", groups=groups, user=None)

        requested_role = request.form.get("role", "student")
        if current_user.role == "teacher":
            requested_role = "student"
        user = User(
            full_name=request.form.get("full_name", "").strip(),
            username=username,
            role=requested_role,
            phone=request.form.get("phone", "").strip(),
            email=request.form.get("email", "").strip(),
        )
        password = request.form.get("password", "")
        if not user.full_name or not user.username or not password:
            flash("To'liq ism, username va parol majburiy.", "error")
            return render_template("users/form.html", groups=groups, user=None)
        user.set_password(password)
        uploaded = save_image(request.files.get("image"))
        if request.files.get("image") and not uploaded:
            return render_template("users/form.html", groups=groups, user=None)
        user.image_path = uploaded
        group_id = request.form.get("group_id", type=int)
        if current_user.role == "teacher" and not can_manage_student(group_id=group_id):
            flash("Siz faqat o'zingizga biriktirilgan guruhlarga talaba qo'sha olasiz.", "error")
            return render_template("users/form.html", groups=groups, user=None)
        db.session.add(user)
        db.session.flush()
        upsert_student_group(user, request.form.get("group_id"))
        log_activity(f"Yangi foydalanuvchi yaratildi: {user.full_name}", "success")
        create_notification(user.id, "Hisob yaratildi", f"Siz uchun login yaratildi. Login: {user.username}", "success")
        db.session.commit()
        flash("Foydalanuvchi muvaffaqiyatli qo'shildi.", "success")
        return redirect(url_for("user_list" if current_user.role == "admin" else "student_list"))

    return render_template("users/form.html", groups=groups, user=None)


@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "teacher")
def edit_user(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if current_user.role == "teacher" and not can_manage_student(target_user=user):
        abort(403)
    groups = Group.query.order_by(Group.name).all() if current_user.role == "admin" else get_teacher_groups(current_user)
    if request.method == "POST":
        new_username = request.form.get("username", "").strip()
        if User.query.filter(User.username == new_username, User.id != user.id).first():
            flash("Bu username band.", "error")
            return render_template("users/form.html", groups=groups, user=user)
        user.full_name = request.form.get("full_name", "").strip()
        user.username = new_username
        user.role = request.form.get("role", user.role) if current_user.role == "admin" else "student"
        user.phone = request.form.get("phone", "").strip()
        user.email = request.form.get("email", "").strip()
        if request.form.get("password", "").strip():
            user.set_password(request.form["password"])
        uploaded = save_image(request.files.get("image"))
        if request.files.get("image") and not uploaded:
            return render_template("users/form.html", groups=groups, user=user)
        if uploaded:
            user.image_path = uploaded
        group_id = request.form.get("group_id", type=int)
        if current_user.role == "teacher" and not can_manage_student(group_id=group_id):
            flash("Talabani faqat biriktirilgan guruhlaringiz ichida saqlashingiz mumkin.", "error")
            return render_template("users/form.html", groups=groups, user=user)
        upsert_student_group(user, request.form.get("group_id"))
        log_activity(f"Foydalanuvchi yangilandi: {user.full_name}", "info")
        db.session.commit()
        flash("Foydalanuvchi ma'lumotlari yangilandi.", "success")
        return redirect(url_for("user_list" if current_user.role == "admin" else "student_list"))
    return render_template("users/form.html", groups=groups, user=user)


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_user(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        flash("O'zingizni o'chira olmaysiz.", "error")
        return redirect(url_for("user_list"))
    full_name = user.full_name
    db.session.delete(user)
    log_activity(f"Foydalanuvchi o'chirildi: {full_name}", "warning")
    db.session.commit()
    flash("Foydalanuvchi o'chirildi.", "success")
    return redirect(url_for("user_list"))


@app.route("/groups", methods=["GET", "POST"])
@login_required
@role_required("admin")
def group_list():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        edit_id = request.form.get("edit_id")
        if not name:
            flash("Guruh nomi majburiy.", "error")
            return redirect(url_for("group_list"))
        if edit_id:
            group = db.session.get(Group, int(edit_id))
            group.name = name
            log_activity(f"Guruh yangilandi: {name}", "info")
            flash("Guruh yangilandi.", "success")
        else:
            if Group.query.filter_by(name=name).first():
                flash("Bunday guruh mavjud.", "error")
                return redirect(url_for("group_list"))
            db.session.add(Group(name=name))
            log_activity(f"Yangi guruh yaratildi: {name}", "success")
            flash("Guruh yaratildi.", "success")
        db.session.commit()
        return redirect(url_for("group_list"))
    groups = Group.query.order_by(Group.name).all()
    return render_template("groups/list.html", groups=groups)


@app.route("/groups/<int:group_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_group(group_id):
    group = db.session.get(Group, group_id) or abort(404)
    if group.student_links or group.subject_links:
        flash("Guruhga bog'langan talabalar yoki fanlar mavjud. Avval bog'lanishlarni olib tashlang.", "error")
        return redirect(url_for("group_list"))
    db.session.delete(group)
    log_activity(f"Guruh o'chirildi: {group.name}", "warning")
    db.session.commit()
    flash("Guruh o'chirildi.", "success")
    return redirect(url_for("group_list"))


@app.route("/subjects", methods=["GET", "POST"])
@login_required
@role_required("admin")
def subject_list():
    teachers = User.query.filter_by(role="teacher").order_by(User.full_name).all()
    groups = Group.query.order_by(Group.name).all()
    editing_subject = None

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        teacher_id = request.form.get("teacher_id") or None
        group_ids = [int(item) for item in request.form.getlist("group_ids")]
        edit_id = request.form.get("edit_id")
        if not name:
            flash("Fan nomi majburiy.", "error")
            return redirect(url_for("subject_list"))
        if edit_id:
            subject = db.session.get(Subject, int(edit_id))
            subject.name = name
            subject.teacher_id = int(teacher_id) if teacher_id else None
            subject.group_links.clear()
            db.session.flush()
            for gid in group_ids:
                db.session.add(SubjectGroup(subject=subject, group_id=gid))
            log_activity(f"Fan yangilandi: {name}", "info")
            flash("Fan yangilandi.", "success")
        else:
            subject = Subject(name=name, teacher_id=int(teacher_id) if teacher_id else None)
            db.session.add(subject)
            db.session.flush()
            for gid in group_ids:
                db.session.add(SubjectGroup(subject=subject, group_id=gid))
            log_activity(f"Yangi fan qo'shildi: {name}", "success")
            flash("Fan yaratildi.", "success")
        db.session.commit()
        return redirect(url_for("subject_list"))

    edit_subject_id = request.args.get("edit")
    if edit_subject_id:
        editing_subject = db.session.get(Subject, int(edit_subject_id))
    subjects = Subject.query.order_by(Subject.name).all()
    return render_template("subjects/list.html", subjects=subjects, teachers=teachers, groups=groups, editing_subject=editing_subject)


@app.route("/subjects/<int:subject_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_subject(subject_id):
    subject = db.session.get(Subject, subject_id) or abort(404)
    db.session.delete(subject)
    log_activity(f"Fan o'chirildi: {subject.name}", "warning")
    db.session.commit()
    flash("Fan o'chirildi.", "success")
    return redirect(url_for("subject_list"))


def available_groups_for_user():
    if current_user.role == "admin":
        return Group.query.order_by(Group.name).all()
    return get_teacher_groups(current_user)


def available_subjects_for_user():
    if current_user.role == "admin":
        return Subject.query.order_by(Subject.name).all()
    return get_visible_subjects_for_teacher(current_user)


@app.route("/attendance", methods=["GET", "POST"])
@login_required
@role_required("admin", "teacher")
def attendance_page():
    groups = available_groups_for_user()
    subjects = available_subjects_for_user()
    selected_group_id = request.values.get("group_id", type=int) or (groups[0].id if groups else None)
    selected_subject_id = request.values.get("subject_id", type=int) or (subjects[0].id if subjects else None)
    selected_date = request.values.get("date") or date.today().isoformat()
    students = []
    existing = {}

    if selected_group_id:
        students = (
            User.query.join(StudentGroup)
            .filter(User.role == "student", StudentGroup.group_id == selected_group_id)
            .order_by(User.full_name)
            .all()
        )
    if students and selected_subject_id:
        attendance_items = Attendance.query.filter_by(group_id=selected_group_id, subject_id=selected_subject_id, date=datetime.strptime(selected_date, "%Y-%m-%d").date()).all()
        existing = {item.student_id: item for item in attendance_items}

    if request.method == "POST" and selected_group_id and selected_subject_id:
        current_date = datetime.strptime(selected_date, "%Y-%m-%d").date()
        for student in students:
            status = request.form.get(f"status_{student.id}", "present")
            reason = request.form.get(f"reason_{student.id}", "").strip()
            record = existing.get(student.id)
            if record:
                record.status = status
                record.reason = reason
            else:
                db.session.add(
                    Attendance(
                        student_id=student.id,
                        group_id=selected_group_id,
                        subject_id=selected_subject_id,
                        date=current_date,
                        status=status,
                        reason=reason,
                    )
                )
        log_activity("Davomat ma'lumotlari saqlandi.", "success")
        db.session.commit()
        flash("Davomat saqlandi.", "success")
        return redirect(url_for("attendance_page", group_id=selected_group_id, subject_id=selected_subject_id, date=selected_date))

    return render_template(
        "attendance/index.html",
        groups=groups,
        subjects=subjects,
        students=students,
        existing=existing,
        selected_group_id=selected_group_id,
        selected_subject_id=selected_subject_id,
        selected_date=selected_date,
    )


@app.route("/grades", methods=["GET", "POST"])
@login_required
@role_required("admin", "teacher")
def grade_page():
    groups = available_groups_for_user()
    subjects = available_subjects_for_user()
    selected_group_id = request.values.get("group_id", type=int) or (groups[0].id if groups else None)
    selected_subject_id = request.values.get("subject_id", type=int) or (subjects[0].id if subjects else None)
    students = []
    latest_grades = {}

    if selected_group_id:
        students = (
            User.query.join(StudentGroup)
            .filter(User.role == "student", StudentGroup.group_id == selected_group_id)
            .order_by(User.full_name)
            .all()
        )
    if students and selected_subject_id:
        for student in students:
            latest = Grade.query.filter_by(student_id=student.id, subject_id=selected_subject_id).order_by(Grade.date.desc()).first()
            latest_grades[student.id] = latest

    if request.method == "POST" and selected_subject_id:
        grade_date = request.form.get("grade_date") or date.today().isoformat()
        for student in students:
            score_raw = request.form.get(f"score_{student.id}", "").strip()
            comment = request.form.get(f"comment_{student.id}", "").strip()
            if not score_raw:
                continue
            grade = Grade(
                student_id=student.id,
                subject_id=selected_subject_id,
                teacher_id=current_user.id if current_user.role == "teacher" else (db.session.get(Subject, selected_subject_id).teacher_id or current_user.id),
                score=float(score_raw),
                comment=comment,
                date=datetime.strptime(grade_date, "%Y-%m-%d").date(),
            )
            db.session.add(grade)
        log_activity("Baholar saqlandi.", "success")
        db.session.commit()
        flash("Baholar muvaffaqiyatli saqlandi.", "success")
        return redirect(url_for("grade_page", group_id=selected_group_id, subject_id=selected_subject_id))

    return render_template(
        "grades/index.html",
        groups=groups,
        subjects=subjects,
        students=students,
        latest_grades=latest_grades,
        selected_group_id=selected_group_id,
        selected_subject_id=selected_subject_id,
    )


@app.route("/assignments", methods=["GET", "POST"])
@login_required
def assignment_page():
    groups = available_groups_for_user() if current_user.role in {"admin", "teacher"} else []
    subjects = available_subjects_for_user() if current_user.role in {"admin", "teacher"} else []
    editing_assignment = None

    if request.method == "POST":
        action = request.form.get("action", "save_assignment")
        if action == "submit_assignment":
            if current_user.role != "student":
                abort(403)
            assignment_id = request.form.get("assignment_id", type=int)
            assignment = db.session.get(Assignment, assignment_id) or abort(404)
            if not current_user.group or assignment.group_id != current_user.group.id:
                abort(403)
            uploaded = save_document(request.files.get("submission_file"))
            if request.files.get("submission_file") and not uploaded:
                return redirect(url_for("assignment_page"))
            submission_text = request.form.get("submission_text", "").strip()
            if not submission_text and not uploaded:
                flash("Kamida izoh yoki fayl yuklang.", "error")
                return redirect(url_for("assignment_page"))
            submission = AssignmentSubmission.query.filter_by(assignment_id=assignment.id, student_id=current_user.id).first()
            if not submission:
                submission = AssignmentSubmission(assignment_id=assignment.id, student_id=current_user.id)
                db.session.add(submission)
            submission.submission_text = submission_text
            if uploaded:
                submission.file_path = uploaded
            submission.submitted_at = datetime.utcnow()
            create_notification(
                assignment.teacher_id,
                "Yangi topshiriq javobi",
                f"{current_user.full_name} {assignment.title} topshirig'iga javob yubordi.",
                "info",
            )
            log_activity(f"Talaba topshiriq yukladi: {assignment.title}", "success")
            db.session.commit()
            flash("Topshiriq javobi muvaffaqiyatli yuborildi.", "success")
            return redirect(url_for("assignment_page"))
        if action == "grade_submission":
            if current_user.role not in {"admin", "teacher"}:
                abort(403)
            submission_id = request.form.get("submission_id", type=int)
            submission = db.session.get(AssignmentSubmission, submission_id) or abort(404)
            if current_user.role == "teacher" and submission.assignment.teacher_id != current_user.id:
                abort(403)
            score = request.form.get("score", "").strip()
            feedback = request.form.get("feedback", "").strip()
            if not score or not feedback:
                flash("Bahoni ham, sababini ham kiriting.", "error")
                return redirect(url_for("assignment_page"))
            numeric_score = float(score)
            if numeric_score < 0 or numeric_score > 5:
                flash("Baho 0 dan 5 gacha bo'lishi kerak.", "error")
                return redirect(url_for("assignment_page"))
            submission.score = numeric_score
            submission.feedback = feedback
            submission.graded_at = datetime.utcnow()
            create_notification(
                submission.student_id,
                "Topshiriq baholandi",
                f"{submission.assignment.title} topshirig'i baholandi. Izoh: {feedback}",
                "success",
            )
            log_activity(f"Topshiriq baholandi: {submission.assignment.title}", "success")
            db.session.commit()
            flash("Topshiriq javobi baholandi.", "success")
            return redirect(url_for("assignment_page"))
        if current_user.role not in {"admin", "teacher"}:
            abort(403)
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        deadline = request.form.get("deadline")
        subject_id = request.form.get("subject_id", type=int)
        group_id = request.form.get("group_id", type=int)
        edit_id = request.form.get("edit_id", type=int)
        if not title or not description or not deadline or not subject_id or not group_id:
            flash("Barcha majburiy maydonlarni to'ldiring.", "error")
            return redirect(url_for("assignment_page"))
        uploaded = save_document(request.files.get("attachment"))
        if request.files.get("attachment") and not uploaded:
            return redirect(url_for("assignment_page"))
        if current_user.role == "teacher":
            if subject_id not in [subject.id for subject in subjects] or group_id not in [group.id for group in groups]:
                abort(403)
        if edit_id:
            assignment = db.session.get(Assignment, edit_id) or abort(404)
            if current_user.role == "teacher" and assignment.teacher_id != current_user.id:
                abort(403)
            assignment.title = title
            assignment.description = description
            assignment.deadline = datetime.strptime(deadline, "%Y-%m-%d").date()
            assignment.subject_id = subject_id
            assignment.group_id = group_id
            if uploaded:
                assignment.attachment_path = uploaded
            log_activity(f"Topshiriq yangilandi: {title}", "info")
            flash("Topshiriq yangilandi.", "success")
        else:
            assignment = Assignment(
                title=title,
                description=description,
                deadline=datetime.strptime(deadline, "%Y-%m-%d").date(),
                subject_id=subject_id,
                group_id=group_id,
                teacher_id=current_user.id if current_user.role == "teacher" else (db.session.get(Subject, subject_id).teacher_id or current_user.id),
                attachment_path=uploaded,
            )
            db.session.add(assignment)
            db.session.flush()
            assignment_link = f"{url_for('assignment_page', focus=assignment.id)}#assignment-{assignment.id}"
            notify_group_students(
                group_id,
                "Yangi topshiriq",
                f"{title} topshirig'i {deadline} gacha berildi.",
                "info",
                target_url=assignment_link,
            )
            log_activity(f"Yangi topshiriq yaratildi: {title}", "success")
            flash("Topshiriq yaratildi.", "success")
        db.session.commit()
        return redirect(url_for("assignment_page"))

    assignments_query = Assignment.query.order_by(Assignment.deadline.asc())
    if current_user.role == "teacher":
        assignments_query = assignments_query.filter_by(teacher_id=current_user.id)
    elif current_user.role == "student":
        if current_user.group:
            assignments_query = assignments_query.filter_by(group_id=current_user.group.id)
        else:
            assignments_query = assignments_query.filter(Assignment.id == -1)
    assignments = assignments_query.all()
    focus_assignment_id = request.args.get("focus", type=int)
    student_submissions = {}
    submission_rows = {}
    if current_user.role == "student":
        student_submissions = {
            item.assignment_id: item
            for item in AssignmentSubmission.query.filter_by(student_id=current_user.id).all()
        }
    elif current_user.role in {"admin", "teacher"}:
        submission_query = AssignmentSubmission.query.join(Assignment, AssignmentSubmission.assignment_id == Assignment.id)
        if current_user.role == "teacher":
            submission_query = submission_query.filter(Assignment.teacher_id == current_user.id)
        for submission in submission_query.order_by(AssignmentSubmission.submitted_at.desc()).all():
            submission_rows.setdefault(submission.assignment_id, []).append(submission)
    edit_id = request.args.get("edit", type=int)
    if edit_id and current_user.role in {"admin", "teacher"}:
        editing_assignment = db.session.get(Assignment, edit_id)
        if editing_assignment and current_user.role == "teacher" and editing_assignment.teacher_id != current_user.id:
            abort(403)
    return render_template(
        "assignments/index.html",
        assignments=assignments,
        groups=groups,
        subjects=subjects,
        editing_assignment=editing_assignment,
        student_submissions=student_submissions,
        submission_rows=submission_rows,
        focus_assignment_id=focus_assignment_id,
    )


@app.route("/notifications")
@login_required
def notification_page():
    filter_type = request.args.get("filter", "").strip()
    notifications = current_user.notifications.order_by(Notification.created_at.desc()).all()
    if filter_type:
        notifications = [item for item in notifications if item.category == filter_type]
    unread_ids = [item.id for item in notifications if not item.is_read]
    if unread_ids:
        Notification.query.filter(Notification.id.in_(unread_ids)).update({"is_read": True}, synchronize_session=False)
        db.session.commit()
    return render_template("notifications/index.html", notifications=notifications, filter_type=filter_type)


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def notification_read_all():
    current_user.notifications.filter_by(is_read=False).update({"is_read": True}, synchronize_session=False)
    db.session.commit()
    flash("Barcha bildirishnomalar o'qilgan deb belgilandi.", "success")
    return redirect(url_for("notification_page"))


@app.route("/notifications/<int:notification_id>/toggle", methods=["POST"])
@login_required
def notification_toggle(notification_id):
    notification = db.session.get(Notification, notification_id) or abort(404)
    if notification.user_id != current_user.id:
        abort(403)
    notification.is_read = not notification.is_read
    db.session.commit()
    flash("Bildirishnoma holati yangilandi.", "success")
    return redirect(url_for("notification_page"))


@app.route("/chat", methods=["GET", "POST"])
@login_required
def chat_page():
    groups = chat_groups_for_user()
    private_users = private_chat_users()
    chat_mode = request.values.get("mode", "group")
    selected_group_id = request.values.get("group_id", type=int) or (groups[0].id if groups else None)
    selected_user_id = request.values.get("user_id", type=int) or (private_users[0].id if private_users else None)

    if chat_mode == "group" and selected_group_id and selected_group_id not in [group.id for group in groups]:
        abort(403)
    if chat_mode == "private" and selected_user_id and selected_user_id not in [user.id for user in private_users]:
        abort(403)

    if request.method == "POST":
        message = request.form.get("message", "").strip()
        uploaded = save_document(request.files.get("chat_file"))
        if request.files.get("chat_file") and not uploaded:
            return redirect(url_for("chat_page", mode=chat_mode, group_id=selected_group_id, user_id=selected_user_id))
        if not message and not uploaded:
            flash("Xabar yoki fayl yuboring.", "error")
            return redirect(url_for("chat_page", mode=chat_mode, group_id=selected_group_id, user_id=selected_user_id))
        chat_mode = request.form.get("mode", chat_mode)
        if chat_mode == "private":
            if not selected_user_id:
                flash("Shaxsiy chat uchun foydalanuvchini tanlang.", "error")
                return redirect(url_for("chat_page", mode="private"))
            recipient = db.session.get(User, selected_user_id) or abort(404)
            private_group_id = resolve_private_group_id(recipient)
            db.session.add(
                ChatMessage(
                    sender_id=current_user.id,
                    receiver_id=selected_user_id,
                    group_id=private_group_id,
                    message=message or "Fayl yuborildi",
                    file_path=uploaded,
                )
            )
        else:
            if not selected_group_id:
                flash("Guruh chat uchun guruh tanlang.", "error")
                return redirect(url_for("chat_page", mode="group"))
            db.session.add(
                ChatMessage(
                    sender_id=current_user.id,
                    group_id=selected_group_id,
                    message=message or "Fayl yuborildi",
                    file_path=uploaded,
                )
            )
        log_activity(f"Chatga yangi xabar yuborildi: {(message or 'fayl')[:40]}", "info")
        db.session.commit()
        flash("Xabar yuborildi.", "success")
        return redirect(url_for("chat_page", mode=chat_mode, group_id=selected_group_id, user_id=selected_user_id))

    messages = []
    if chat_mode == "private" and selected_user_id:
        messages = (
            ChatMessage.query.filter(
                or_(
                    and_(ChatMessage.sender_id == current_user.id, ChatMessage.receiver_id == selected_user_id),
                    and_(ChatMessage.sender_id == selected_user_id, ChatMessage.receiver_id == current_user.id),
                )
            )
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
        unseen_ids = [item.id for item in messages if item.receiver_id == current_user.id and item.seen_at is None]
        if unseen_ids:
            ChatMessage.query.filter(ChatMessage.id.in_(unseen_ids)).update({"seen_at": datetime.utcnow()}, synchronize_session=False)
            db.session.commit()
    elif selected_group_id:
        messages = ChatMessage.query.filter_by(group_id=selected_group_id).order_by(ChatMessage.created_at.asc()).all()
    return render_template(
        "chat/index.html",
        groups=groups,
        private_users=private_users,
        messages=messages,
        selected_group_id=selected_group_id,
        selected_user_id=selected_user_id,
        chat_mode=chat_mode,
    )


@app.route("/assignments/<int:assignment_id>/delete", methods=["POST"])
@login_required
@role_required("admin", "teacher")
def delete_assignment(assignment_id):
    assignment = db.session.get(Assignment, assignment_id) or abort(404)
    if current_user.role == "teacher" and assignment.teacher_id != current_user.id:
        abort(403)
    title = assignment.title
    db.session.delete(assignment)
    log_activity(f"Topshiriq o'chirildi: {title}", "warning")
    db.session.commit()
    flash("Topshiriq o'chirildi.", "success")
    return redirect(url_for("assignment_page"))


@app.route("/reports")
@login_required
@role_required("admin")
def reports_page():
    groups = Group.query.order_by(Group.name).all()
    subjects = Subject.query.order_by(Subject.name).all()
    group_id = request.args.get("group_id", type=int)
    subject_id = request.args.get("subject_id", type=int)
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    attendance_query = (
        Attendance.query
        .join(User, Attendance.student_id == User.id)
        .join(Group, Attendance.group_id == Group.id)
        .join(Subject, Attendance.subject_id == Subject.id)
    )
    grade_query = (
        Grade.query
        .join(User, Grade.student_id == User.id)
        .join(Subject, Grade.subject_id == Subject.id)
    )

    if group_id:
        attendance_query = attendance_query.filter(Attendance.group_id == group_id)
        grade_query = grade_query.join(StudentGroup, StudentGroup.student_id == Grade.student_id).filter(StudentGroup.group_id == group_id)
    if subject_id:
        attendance_query = attendance_query.filter(Attendance.subject_id == subject_id)
        grade_query = grade_query.filter(Grade.subject_id == subject_id)
    if date_from:
        date_from_value = datetime.strptime(date_from, "%Y-%m-%d").date()
        attendance_query = attendance_query.filter(Attendance.date >= date_from_value)
        grade_query = grade_query.filter(Grade.date >= date_from_value)
    if date_to:
        date_to_value = datetime.strptime(date_to, "%Y-%m-%d").date()
        attendance_query = attendance_query.filter(Attendance.date <= date_to_value)
        grade_query = grade_query.filter(Grade.date <= date_to_value)

    attendance_rows = attendance_query.order_by(Attendance.date.desc()).limit(100).all()
    grade_rows = grade_query.order_by(Grade.date.desc()).limit(100).all()
    export_type = request.args.get("export")
    if export_type in {"attendance", "grades"}:
        output = StringIO()
        writer = csv.writer(output)
        if export_type == "attendance":
            writer.writerow(["Talaba", "Guruh", "Fan", "Sana", "Holat", "Sabab"])
            for row in attendance_rows:
                writer.writerow([row.student.full_name, row.group.name, row.subject.name, row.date.isoformat(), row.status, row.reason or ""])
            filename = "davomat-hisoboti.csv"
        else:
            writer.writerow(["Talaba", "Fan", "Baho", "Izoh", "Sana"])
            for row in grade_rows:
                writer.writerow([row.student.full_name, row.subject.name, row.score, row.comment or "", row.date.isoformat()])
            filename = "baho-hisoboti.csv"
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    return render_template("reports/index.html", groups=groups, subjects=subjects, attendance_rows=attendance_rows, grade_rows=grade_rows)


@app.route("/calendar")
@login_required
def calendar_page():
    if current_user.role == "student":
        assignments = Assignment.query.filter_by(group_id=current_user.group.id).order_by(Assignment.deadline.asc()).all() if current_user.group else []
    elif current_user.role == "teacher":
        assignments = Assignment.query.filter_by(teacher_id=current_user.id).order_by(Assignment.deadline.asc()).all()
    else:
        assignments = Assignment.query.order_by(Assignment.deadline.asc()).all()
    events = [
        {
            "title": item.title,
            "date": item.deadline,
            "type": "topshiriq",
            "meta": f"{item.subject.name} • {item.group.name}",
        }
        for item in assignments[:20]
    ]
    recent_grades = []
    if current_user.role == "student":
        recent_grades = Grade.query.filter_by(student_id=current_user.id).order_by(Grade.date.desc()).limit(6).all()
    return render_template("calendar.html", events=events, recent_grades=recent_grades)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile_page():
    if request.method == "POST":
        new_password = request.form.get("new_password", "").strip()
        current_password = request.form.get("current_password", "")
        if new_password:
            if not current_user.check_password(current_password):
                flash("Joriy parol noto'g'ri.", "error")
                return render_template("profile.html", recent_activities=current_user.activities.order_by(ActivityLog.created_at.desc()).limit(10).all())
            current_user.set_password(new_password)
        current_user.full_name = request.form.get("full_name", "").strip()
        current_user.phone = request.form.get("phone", "").strip()
        current_user.email = request.form.get("email", "").strip()
        uploaded = save_image(request.files.get("image"))
        if request.files.get("image") and not uploaded:
            return render_template("profile.html", recent_activities=current_user.activities.order_by(ActivityLog.created_at.desc()).limit(10).all())
        if uploaded:
            current_user.image_path = uploaded
        log_activity("Profil ma'lumotlari yangilandi.", "info")
        db.session.commit()
        flash("Profil yangilandi.", "success")
        return redirect(url_for("profile_page"))
    return render_template("profile.html", recent_activities=current_user.activities.order_by(ActivityLog.created_at.desc()).limit(10).all())


def ensure_storage():
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["DOCUMENT_UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)


def ensure_schema_updates():
    inspector = inspect(db.engine)
    assignment_columns = {column["name"] for column in inspector.get_columns("assignments")} if inspector.has_table("assignments") else set()
    if assignment_columns and "attachment_path" not in assignment_columns:
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE assignments ADD COLUMN attachment_path VARCHAR(255)"))

    notification_columns = {column["name"] for column in inspector.get_columns("notifications")} if inspector.has_table("notifications") else set()
    if notification_columns and "target_url" not in notification_columns:
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE notifications ADD COLUMN target_url VARCHAR(255)"))

    attendance_columns = {column["name"] for column in inspector.get_columns("attendance")} if inspector.has_table("attendance") else set()
    if attendance_columns and "reason" not in attendance_columns:
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE attendance ADD COLUMN reason VARCHAR(255)"))

    chat_columns = {column["name"] for column in inspector.get_columns("chat_messages")} if inspector.has_table("chat_messages") else set()
    with db.engine.begin() as connection:
        if chat_columns and "receiver_id" not in chat_columns:
            connection.execute(text("ALTER TABLE chat_messages ADD COLUMN receiver_id INTEGER"))
        if chat_columns and "file_path" not in chat_columns:
            connection.execute(text("ALTER TABLE chat_messages ADD COLUMN file_path VARCHAR(255)"))
        if chat_columns and "seen_at" not in chat_columns:
            connection.execute(text("ALTER TABLE chat_messages ADD COLUMN seen_at DATETIME"))

    if not inspector.has_table("assignment_submissions"):
        AssignmentSubmission.__table__.create(db.engine)


with app.app_context():
    ensure_storage()
    db.create_all()
    ensure_schema_updates()
    seed_database()


if __name__ == "__main__":
    app.run(debug=True)
