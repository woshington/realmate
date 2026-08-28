from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Iterator, final

from django.db import transaction

from properties.importers.schemas import PropertyData, ImportResult
from properties.models import Property


class PropertyImporter(ABC):
    source: str

    @abstractmethod
    def extract(self) -> Iterator[Any]:
        raise NotImplementedError

    @abstractmethod
    def transform(self, raw: Any) -> PropertyData:
        raise NotImplementedError

    @final
    def load(self) -> ImportResult:
        result = ImportResult()
        imported_at = datetime.now(timezone.utc)

        for raw in self.extract():
            try:
                data = self.transform(raw)
            except (ValueError, KeyError) as exc:
                result.errors.append(f"Registro ignorado ({exc}): {raw!r}")
                result.skipped += 1
                continue

            _, created = self._upsert(data, imported_at)
            if created:
                result.created += 1
            else:
                result.updated += 1

        return result

    @staticmethod
    def _upsert(data: PropertyData, imported_at: datetime) -> tuple[Property, bool]:
        with transaction.atomic():
            return Property.objects.update_or_create(
                code=data.code,
                defaults={
                    "transaction_type": data.transaction_type,
                    "neighborhood": data.neighborhood,
                    "price": data.price,
                    "bedrooms": data.bedrooms,
                    "address": data.address,
                    "description": data.description,
                    "source": data.source,
                    "source_reference": data.source_reference,
                    "imported_at": imported_at,
                },
            )