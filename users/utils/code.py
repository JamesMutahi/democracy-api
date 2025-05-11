import random

from users.models import Code


def generate_code():
    code = (random.randint(1000, 9999))
    while Code.objects.filter(code=code).exists():
        code = (random.randint(1000, 9999))
    return code