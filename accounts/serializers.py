from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

User = get_user_model()


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

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data['username'],
            email=validated_data.get('email', ''),
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
            # Django's User.email is blank-able; for a student it's how the
            # admin reaches them, so require it here.
            'email': {'required': True, 'allow_blank': False},
        }

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
