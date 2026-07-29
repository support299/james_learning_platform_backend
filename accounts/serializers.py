from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from ghl import services as ghl_services
from ghl.models import GhlToken, GhlUser
from ghl.serializers import GhlUserSerializer

User = get_user_model()


def email_taken(email, exclude_pk=None):
    """Is this email already on another account? Compared case-insensitively,
    since that's how login looks accounts up."""
    others = User.objects.filter(email__iexact=email.strip())
    if exclude_pk is not None:
        others = others.exclude(pk=exclude_pk)
    return others.exists()


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Log in with an email address and password.

    The user model still keys on `username`, so we resolve the email to its
    account and let the normal SimpleJWT flow do the authentication.
    """

    email = serializers.EmailField(write_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The parent declares a required `username` field; clients send an
        # email instead, so drop it.
        self.fields.pop(self.username_field, None)

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Carried in the token so anything holding it knows the role without a
        # second round trip. It's a convenience for the client, not a
        # permission — the server still checks is_staff on every request.
        token['is_admin'] = user.is_staff
        return token

    def validate(self, attrs):
        email = attrs.pop('email', '').strip()
        user = User.objects.filter(email__iexact=email).order_by('pk').first()
        # An unknown email falls through with a username that can't match, so
        # the failure is the same "no active account" either way and the
        # response doesn't reveal which emails are registered.
        attrs[self.username_field] = user.get_username() if user else ''
        data = super().validate(attrs)
        # Alongside the tokens, so the client can style the admin UI on the
        # login response instead of waiting for /me.
        data['is_admin'] = self.user.is_staff
        return data


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        # `is_staff` tells the client whether to show the admin area.
        fields = ['id', 'username', 'email', 'is_staff']


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, validators=[validate_password]
    )

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password']
        extra_kwargs = {
            # The email is how you log in, so it has to be there and be unique.
            'email': {'required': True, 'allow_blank': False},
        }

    def validate_email(self, value):
        if email_taken(value):
            raise serializers.ValidationError(
                'An account with this email already exists.'
            )
        return value

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
        )


class StudentSerializer(serializers.ModelSerializer):
    """A student as managed from the admin area. Students are ordinary users
    with is_staff=False, so this is the plain User model minus the staff and
    permission machinery."""

    # Required when creating; optional on update, where a blank/absent value
    # leaves the existing password alone.
    password = serializers.CharField(
        write_only=True, required=False, validators=[validate_password]
    )

    # Optional: the student's GoHighLevel user id. Given one, we look the user
    # up in GHL, mirror them into `ghl_users` and link the row to this student.
    # Sending '' clears an existing link. Reads come back as `ghl_user`.
    ghl_user_id = serializers.CharField(
        write_only=True, required=False, allow_blank=True
    )
    ghl_user = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'is_active',
            'date_joined',
            'last_login',
            'password',
            'ghl_user_id',
            'ghl_user',
        ]
        read_only_fields = ['date_joined', 'last_login']
        extra_kwargs = {
            # Django's User.email is blank-able; for a student it's both how
            # the admin reaches them and how they log in, so require it here.
            'email': {'required': True, 'allow_blank': False},
        }

    def validate_email(self, value):
        exclude_pk = self.instance.pk if self.instance else None
        if email_taken(value, exclude_pk=exclude_pk):
            raise serializers.ValidationError(
                'An account with this email already exists.'
            )
        return value

    def validate_password(self, value):
        if not value and self.instance is None:
            raise serializers.ValidationError('This field is required.')
        return value

    def get_ghl_user(self, student):
        # An unlinked student has no reverse object at all, so this is a
        # getattr with a default rather than a plain attribute access.
        ghl_user = getattr(student, 'ghl_user', None)
        return GhlUserSerializer(ghl_user).data if ghl_user else None

    def validate_ghl_user_id(self, value):
        value = (value or '').strip()
        if not value:
            return ''
        # One GHL user maps to one student; say so plainly instead of letting
        # the one-to-one constraint surface as a 500.
        taken = GhlUser.objects.filter(ghl_id=value).exclude(student=None)
        if self.instance is not None:
            taken = taken.exclude(student=self.instance)
        if taken.exists():
            raise serializers.ValidationError(
                'That GoHighLevel user is already linked to another student.'
            )
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get('password'):
            raise serializers.ValidationError(
                {'password': 'This field is required.'}
            )
        # Look the GHL user up here, before anything is written: a bad id then
        # fails the whole request rather than leaving a student with no link.
        self._ghl_payload = None
        if attrs.get('ghl_user_id'):
            self._ghl_payload = self._fetch_ghl_user(attrs['ghl_user_id'])
        return attrs

    def _fetch_ghl_user(self, ghl_user_id):
        try:
            return ghl_services.fetch_user(ghl_user_id)
        except GhlToken.DoesNotExist:
            raise serializers.ValidationError(
                {
                    'ghl_user_id': (
                        'GoHighLevel is not connected; install the app first.'
                    )
                }
            )
        except ghl_services.GhlError as exc:
            detail = (
                'No GoHighLevel user with that id.'
                if exc.status_code == 404
                else f'Could not reach GoHighLevel: {exc}'
            )
            raise serializers.ValidationError({'ghl_user_id': detail})

    def _apply_ghl_link(self, student, ghl_user_id):
        # Absent field → leave any existing link alone; blank → unlink.
        if ghl_user_id is None:
            return
        if not ghl_user_id:
            GhlUser.objects.filter(student=student).update(student=None)
            return
        try:
            ghl_services.link_user_to_student(self._ghl_payload, student)
        except ghl_services.GhlError as exc:
            # validate_ghl_user_id already rejects a taken user; this catches
            # the same claim made between that check and this write.
            raise serializers.ValidationError({'ghl_user_id': str(exc)})

    @transaction.atomic
    def create(self, validated_data):
        ghl_user_id = validated_data.pop('ghl_user_id', None)
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        self._apply_ghl_link(user, ghl_user_id)
        return user

    @transaction.atomic
    def update(self, instance, validated_data):
        ghl_user_id = validated_data.pop('ghl_user_id', None)
        password = validated_data.pop('password', None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.save()
        self._apply_ghl_link(instance, ghl_user_id)
        return instance
