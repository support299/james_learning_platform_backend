import uuid

from django.conf import settings
from django.db import migrations, models


def fill_uuids_and_locations(apps, schema_editor):
    GhlUser = apps.get_model('ghl', 'GhlUser')
    for row in GhlUser.objects.all():
        updates = []
        if getattr(row, 'id', None) is None:
            row.id = uuid.uuid4()
            updates.append('id')
        if row.location_id is None:
            row.location_id = ''
            updates.append('location_id')
        if updates:
            row.save(update_fields=updates)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('ghl', '0003_rename_student_ghluser_user'),
    ]

    operations = [
        migrations.AddField(
            model_name='ghluser',
            name='id',
            field=models.UUIDField(default=uuid.uuid4, null=True),
        ),
        migrations.RunPython(fill_uuids_and_locations, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='ghluser',
            name='location_id',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.RunSQL(
            sql="""
                UPDATE ghl_users SET location_id = '' WHERE location_id IS NULL;
                ALTER TABLE ghl_users DROP CONSTRAINT ghl_users_pkey;
                ALTER TABLE ghl_users ALTER COLUMN id SET NOT NULL;
                ALTER TABLE ghl_users ADD PRIMARY KEY (id);
            """,
            reverse_sql="""
                ALTER TABLE ghl_users DROP CONSTRAINT ghl_users_pkey;
                ALTER TABLE ghl_users ADD PRIMARY KEY (ghl_id);
            """,
            state_operations=[
                migrations.AlterField(
                    model_name='ghluser',
                    name='ghl_id',
                    field=models.CharField(max_length=64),
                ),
                migrations.AlterField(
                    model_name='ghluser',
                    name='id',
                    field=models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
            ],
        ),
        migrations.AlterField(
            model_name='ghluser',
            name='user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='ghl_users',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddIndex(
            model_name='ghluser',
            index=models.Index(fields=['ghl_id'], name='ghl_users_ghl_id_idx'),
        ),
        migrations.AddConstraint(
            model_name='ghluser',
            constraint=models.UniqueConstraint(
                fields=('ghl_id', 'location_id'),
                name='ghl_users_ghl_id_location_uniq',
            ),
        ),
    ]
