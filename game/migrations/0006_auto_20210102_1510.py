# Generated by Django 3.1.3 on 2021-01-02 09:40

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0005_auto_20210102_1432'),
    ]

    operations = [
        migrations.AlterField(
            model_name='game',
            name='level',
            field=models.CharField(blank=True, max_length=10, null=True),
        ),
    ]
