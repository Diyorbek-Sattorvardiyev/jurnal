from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = "elektron-jurnal-maxfiy-kalit"
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{BASE_DIR / 'database.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
    DOCUMENT_UPLOAD_FOLDER = BASE_DIR / "static" / "documents"
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
    ALLOWED_DOCUMENT_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx"}
