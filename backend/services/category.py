"""類別（文學分類）服務。"""
from .. import db


def list_categories():
    return db.list_categories()


def get_or_none(name_or_id):
    return db.get_category(name_or_id)