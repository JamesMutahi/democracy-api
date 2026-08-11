import firebase_admin
from firebase_admin import credentials
from django.conf import settings

def get_firebase_app():
    try:
        return firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(settings.FIREBASE_SERVICE_ACCOUNT_PATH)
        return firebase_admin.initialize_app(cred)