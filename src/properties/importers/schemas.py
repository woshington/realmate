from decimal import Decimal

from pydantic import BaseModel, Field


class PropertyData(BaseModel):
    code: str
    transaction_type: str
    neighborhood: str
    price: Decimal
    bedrooms: int
    address: str
    description: str
    source: str
    source_reference: str


class ImportResult(BaseModel):

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return self.created + self.updated + self.skipped