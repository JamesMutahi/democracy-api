# Generated by Django 5.2.1 on 2025-05-26 16:56

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('social', '0002_rename_active_post_is_active_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='repost_of',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reposts', to='social.post'),
        ),
    ]
