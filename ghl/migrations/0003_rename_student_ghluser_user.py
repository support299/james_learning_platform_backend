from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('ghl', '0002_ghluser'),
    ]

    operations = [
        migrations.RenameField(
            model_name='ghluser',
            old_name='student',
            new_name='user',
        ),
    ]
