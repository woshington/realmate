from pydantic import BaseModel, Field


class PropertyOutput(BaseModel):
    code: str = Field()
    price: int = Field()
    neighborhood: str = Field()
    bedrooms: int = Field()
    address: str = Field()
    description: str = Field()


class PropertySearchResult(BaseModel):
    properties: list[PropertyOutput] = Field(default_factory=list)
    guidance: str = Field()


class FaqEntry(BaseModel):
    pergunta: str = Field()
    resposta: str = Field()


class AgentReply(BaseModel):
    message: str = Field()
    recommended_properties: list[PropertyOutput] = Field(default_factory=list)
