# -*- coding: utf-8 -*-
# Generated by Django 1.9.7 on 2016-08-18 23:36
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tournament', '0075_auto_20160818_0456'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='season',
            options={'permissions': (('manage_players', 'Can manage players'),)},
        ),
    ]