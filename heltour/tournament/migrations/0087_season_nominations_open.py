# -*- coding: utf-8 -*-
# Generated by Django 1.9.7 on 2016-09-03 17:05
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tournament', '0086_auto_20160901_2352'),
    ]

    operations = [
        migrations.AddField(
            model_name='season',
            name='nominations_open',
            field=models.BooleanField(default=False),
        ),
    ]