from django.core.validators import RegexValidator

PHONE_REGEX = r"^\+\d{12,13}$"

phone_validator = RegexValidator(
    regex=PHONE_REGEX,
    message="Telefone deve seguir o formato +5588999999999 (DDI + DDD + número).",
)
