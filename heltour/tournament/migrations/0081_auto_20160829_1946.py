# -*- coding: utf-8 -*-
# Generated by Django 1.9.7 on 2016-08-29 19:46
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tournament', '0080_auto_20160824_2233'),
    ]

    operations = [
        migrations.CreateModel(
            name='SeasonDocument',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date_created', models.DateTimeField(auto_now_add=True)),
                ('date_modified', models.DateTimeField(auto_now=True)),
                ('tag', models.SlugField(help_text='The document will be accessible at /{league_tag}/season/{season_tag}/document/{document_tag}/')),
                ('type', models.CharField(blank=True, choices=[('links', 'Links')], max_length=255)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tournament.Document')),
                ('season', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tournament.Season')),
            ],
        ),
        migrations.AlterUniqueTogether(
            name='seasondocument',
            unique_together=set([('season', 'tag')]),
        ),
    ]