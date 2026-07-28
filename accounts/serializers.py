from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

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

    def validate(self, attrs):
        email = attrs.pop('email', '').strip()
        user = User.objects.filter(email__iexact=email).order_by('pk').first()
        # An unknown email falls through with a username that can't match, so
        # the failure is the same "no active account" either way and the
        # response doesn't reveal which emails are registered.
        attrs[self.username_field] = user.get_username() if user else ''
        return super().validate(attrs)


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

    def validate(self, attrs):
        if self.instance is None and not attrs.get('password'):
            raise serializers.ValidationError(
                {'password': 'This field is required.'}
            )
        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance
