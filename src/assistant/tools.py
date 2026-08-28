from decimal import Decimal
from typing import Optional

from conversations.models import PropertyRecommendation
from properties.models import Property


def search_properties(
    conversation_id: str,
    code: Optional[str] = None,
    transaction_type: Optional[str] = None,
    neighborhood: Optional[str] = None,
    min_price: Optional[Decimal] = None,
    max_price: Optional[Decimal] = None,
):
    already_recommended = list(PropertyRecommendation.objects.filter(conversation_id=conversation_id).values_list(
        "property_id", flat=True
    ))

    if code:
        return Property.objects.filter(code=code).exclude(id__in=already_recommended).first()

    if transaction_type is None:
        raise ValueError("transaction_type is required when code is not provided")

    if neighborhood is None:
        raise ValueError("neighborhood is required when code is not provided")

    if min_price is None and max_price is None:
        raise ValueError("min_price and max_price are required when code is not provided")

    params = {
        "transaction_type": transaction_type,
        "neighborhood": neighborhood,
    }
    if min_price:
        params["price__gte"] = min_price
    if max_price:
        params["price__lte"] = max_price

    return Property.objects.filter(**params).exclude(id__in=already_recommended).first()

