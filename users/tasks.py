from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone

from users.models import Code
from users.utils.code import generate_code

User = get_user_model()
logger = get_task_logger(__name__)


@shared_task(bind=True)
def send_code(self, user_id, subject, template_name):
    """
    Task to send an e-mail
    """
    user = User.objects.get(id=user_id)
    code_qs = Code.objects.filter(user=user)
    code = generate_code()
    if code_qs.exists():
        code_obj = code_qs.first()
        time_generated = code_qs.first().created_at
        time_now = timezone.now()
        delta = time_now - time_generated
        if not delta.seconds > 300:
            code = code_obj.code
        else:
            code_obj.delete()
            Code.objects.create(user=user, code=code)
    else:
        Code.objects.create(user=user, code=code)
    context = {
        'full_name': user.get_full_name(),
        'code': code,
    }
    msg_html = render_to_string(template_name, context)
    msg = EmailMessage(subject=subject, body=msg_html, from_email=settings.DEFAULT_FROM_EMAIL, to=[user.email])
    msg.content_subtype = "html"
    return msg.send()