from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health endpoint response."""

    status: Literal["ok"]

