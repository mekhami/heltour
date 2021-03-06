# -*- coding: utf-8 -*-
# Generated by Django 1.9.7 on 2016-07-28 05:05
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tournament', '0029_auto_20160727_0214'),
    ]

    operations = [
        migrations.CreateModel(
            name='LonePlayerPairing',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date_created', models.DateTimeField(auto_now_add=True)),
                ('date_modified', models.DateTimeField(auto_now=True)),
                ('player_pairing', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='tournament.Pairing')),
                ('round', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tournament.Round')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='TeamPlayerPairing',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date_created', models.DateTimeField(auto_now_add=True)),
                ('date_modified', models.DateTimeField(auto_now=True)),
                ('board_number', models.PositiveIntegerField(choices=[(1, '1'), (2, '2'), (3, '3'), (4, '4'), (5, '5'), (6, '6')])),
                ('player_pairing', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='tournament.Pairing')),
                ('team_pairing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tournament.TeamPairing')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.RunSQL('''INSERT INTO tournament_teamplayerpairing ( team_pairing_id, player_pairing_id, board_number, date_created, date_modified )
                             SELECT team_pairing_id, id, board_number, date_created, date_modified FROM tournament_pairing'''),
    ]
