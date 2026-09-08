import uuid

from django.conf import settings
from django.db import migrations, models


def fill_uuids_and_locations(apps, schema_editor):
    """Give every existing row its own UUID.

    PostgreSQL applies AddField(default=uuid.uuid4) as one constant DEFAULT, so
    every current row would share the same id and ADD PRIMARY KEY would fail.
    Always mint a new id here; do not keep the column default.
    """
    GhlUser = apps.get_model('ghl', 'GhlUser')
    for row in GhlUser.objects.all().iterator():
        row.id = uuid.uuid4()
        if row.location_id is None:
            row.location_id = ''
        row.save(update_fields=['id', 'location_id'])


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
                UPDATE ghl_users SET id = gen_random_uuid();
                ALTER TABLE ghl_users DROP CONSTRAINT ghl_users_pkey;
                ALTER TABLE ghl_users ALTER COLUMN id SET NOT NULL;
                ALTER TABLE ghl_users ADD PRIMARY KEY (id);
            """,
            # Cannot restore ghl_id as PK once the same user exists in
            # more than one location. Do not migrate backwards past 0004.
            reverse_sql=None,
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
