# Generated by Django 5.2.1 on 2025-07-02 15:32

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('poll', '0003_reason'),
    ]

    operations = [
        migrations.AlterField(
            model_name='reason',
            name='poll',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reasons', to='poll.poll'),
        ),
    ]
