from django.contrib.postgres.operations import CreateExtension
from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('posts', '0005_remove_post_image5_remove_post_image6'),
    ]

    operations = [
        CreateExtension('pg_trgm'),
    ]