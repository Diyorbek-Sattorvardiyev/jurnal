from datetime import datetime, date

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, index=True)
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    image_path = db.Column(db.String(255))

    taught_subjects = db.relationship("Subject", back_populates="teacher", lazy="dynamic")
    student_group = db.relationship("StudentGroup", back_populates="student", uselist=False, cascade="all, delete-orphan")
    attendance_records = db.relationship("Attendance", back_populates="student", lazy="dynamic", foreign_keys="Attendance.student_id")
    grade_records = db.relationship("Grade", back_populates="student", lazy="dynamic", foreign_keys="Grade.student_id")
    grades_given = db.relationship("Grade", back_populates="teacher", lazy="dynamic", foreign_keys="Grade.teacher_id")
    assignments_created = db.relationship("Assignment", back_populates="teacher", lazy="dynamic", foreign_keys="Assignment.teacher_id")
    activities = db.relationship("ActivityLog", back_populates="user", lazy="dynamic")
    notifications = db.relationship("Notification", back_populates="user", lazy="dynamic", cascade="all, delete-orphan")
    chat_messages = db.relationship(
        "ChatMessage",
        back_populates="sender",
        lazy="dynamic",
        cascade="all, delete-orphan",
        foreign_keys="ChatMessage.sender_id",
    )
    assignment_submissions = db.relationship("AssignmentSubmission", back_populates="student", lazy="dynamic", cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def group(self):
        return self.student_group.group if self.student_group else None

    def __repr__(self):
        return f"<User {self.username}>"


class Group(TimestampMixin, db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)

    student_links = db.relationship("StudentGroup", back_populates="group", cascade="all, delete-orphan")
    subject_links = db.relationship("SubjectGroup", back_populates="group", cascade="all, delete-orphan")
    attendance_records = db.relationship("Attendance", back_populates="group", lazy="dynamic")
    assignments = db.relationship("Assignment", back_populates="group", lazy="dynamic")

    @property
    def students(self):
        return [link.student for link in self.student_links]

    @property
    def subjects(self):
        return [link.subject for link in self.subject_links]


class StudentGroup(TimestampMixin, db.Model):
    __tablename__ = "student_groups"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False)

    student = db.relationship("User", back_populates="student_group")
    group = db.relationship("Group", back_populates="student_links")


class Subject(TimestampMixin, db.Model):
    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    teacher = db.relationship("User", back_populates="taught_subjects")
    group_links = db.relationship("SubjectGroup", back_populates="subject", cascade="all, delete-orphan")
    attendance_records = db.relationship("Attendance", back_populates="subject", lazy="dynamic")
    grades = db.relationship("Grade", back_populates="subject", lazy="dynamic")
    assignments = db.relationship("Assignment", back_populates="subject", lazy="dynamic")

    @property
    def groups(self):
        return [link.group for link in self.group_links]


class SubjectGroup(TimestampMixin, db.Model):
    __tablename__ = "subject_groups"

    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False)

    __table_args__ = (db.UniqueConstraint("subject_id", "group_id", name="uq_subject_group"),)

    subject = db.relationship("Subject", back_populates="group_links")
    group = db.relationship("Group", back_populates="subject_links")


class Attendance(TimestampMixin, db.Model):
    __tablename__ = "attendance"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    status = db.Column(db.String(20), nullable=False, default="present")
    reason = db.Column(db.String(255))

    __table_args__ = (db.UniqueConstraint("student_id", "subject_id", "date", name="uq_attendance_student_subject_date"),)

    student = db.relationship("User", back_populates="attendance_records", foreign_keys=[student_id])
    group = db.relationship("Group", back_populates="attendance_records")
    subject = db.relationship("Subject", back_populates="attendance_records")


class Grade(TimestampMixin, db.Model):
    __tablename__ = "grades"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    score = db.Column(db.Float, nullable=False)
    comment = db.Column(db.Text)
    date = db.Column(db.Date, nullable=False, default=date.today)

    student = db.relationship("User", back_populates="grade_records", foreign_keys=[student_id])
    subject = db.relationship("Subject", back_populates="grades")
    teacher = db.relationship("User", back_populates="grades_given", foreign_keys=[teacher_id])


class Assignment(TimestampMixin, db.Model):
    __tablename__ = "assignments"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=False)
    deadline = db.Column(db.Date, nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    attachment_path = db.Column(db.String(255))

    subject = db.relationship("Subject", back_populates="assignments")
    group = db.relationship("Group", back_populates="assignments")
    teacher = db.relationship("User", back_populates="assignments_created")
    submissions = db.relationship("AssignmentSubmission", back_populates="assignment", lazy="dynamic", cascade="all, delete-orphan")


class ActivityLog(TimestampMixin, db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    message = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50), default="info")

    user = db.relationship("User", back_populates="activities")


class Notification(TimestampMixin, db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title = db.Column(db.String(150), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50), default="info", nullable=False)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    target_url = db.Column(db.String(255))

    user = db.relationship("User", back_populates="notifications")


class ChatMessage(TimestampMixin, db.Model):
    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), index=True)
    message = db.Column(db.Text, nullable=False)
    file_path = db.Column(db.String(255))
    seen_at = db.Column(db.DateTime)

    sender = db.relationship("User", back_populates="chat_messages", foreign_keys=[sender_id])
    group = db.relationship("Group")
    receiver = db.relationship("User", foreign_keys=[receiver_id])


class AssignmentSubmission(TimestampMixin, db.Model):
    __tablename__ = "assignment_submissions"

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignments.id"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    submission_text = db.Column(db.Text)
    file_path = db.Column(db.String(255))
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    score = db.Column(db.Float)
    feedback = db.Column(db.Text)
    graded_at = db.Column(db.DateTime)

    __table_args__ = (db.UniqueConstraint("assignment_id", "student_id", name="uq_assignment_student_submission"),)

    assignment = db.relationship("Assignment", back_populates="submissions")
    student = db.relationship("User", back_populates="assignment_submissions")
