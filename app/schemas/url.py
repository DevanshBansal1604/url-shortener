"""HTTP request and response contracts."""

from pydantic import BaseModel, ConfigDict, HttpUrl


class URLCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    long_url: HttpUrl


class URLCreated(BaseModel):
    code: str
    short_url: HttpUrl


class URLMapping(BaseModel):
    """Validated lookup result shared by database and cache retrieval."""

    model_config = ConfigDict(from_attributes=True)

    original_url: HttpUrl
    short_code: str
