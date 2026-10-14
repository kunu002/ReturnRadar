from typing import Annotated, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator


class ProcessTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=180)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=200)


class ReturnRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=180)
    product_id: str = Field(min_length=1, max_length=180)
    variant_id: str = Field(min_length=1, max_length=180)
    order_id: str = Field(min_length=1, max_length=180)
    order_item_id: str = Field(min_length=1, max_length=180)
    return_id: str = Field(min_length=1, max_length=180)
    refund_id: str | None = Field(default=None, max_length=180)
    reason: str = Field(min_length=1, max_length=2000)
    returned_at: str
    status: Literal["requested", "open", "closed", "received", "canceled", "declined"] = "requested"
    source: Literal["synthetic", "csv", "shopify", "woocommerce"] = "csv"
    issue: str | None = None
    listing_snapshot_id: str | None = None


class FitInput(BaseModel):
    width: float = Field(gt=0, le=100)
    height: float = Field(gt=0, le=100)
    depth: float = Field(gt=0, le=30)
    sleeve: bool = False


class Approval(BaseModel):
    status: Literal["approved", "dismissed", "draft"]
    text: str = Field(min_length=5, max_length=1200)
    width: float | None = Field(default=None, gt=0, le=100)
    height: float | None = Field(default=None, gt=0, le=100)
    depth: float | None = Field(default=None, gt=0, le=30)
    measurements_verified: bool = False
    guidance_verified: bool = False
    checklist: list[Annotated[str,Field(min_length=3,max_length=250)]] | None = Field(default=None,min_length=2,max_length=8)

    @field_validator("checklist",mode="before")
    @classmethod
    def trim_checklist(cls,value):
        return [item.strip() if isinstance(item,str) else item for item in value] if isinstance(value,list) else value


Issue = Literal["Laptop fit", "Zipper quality", "Color mismatch", "Strap comfort", "Size & fit", "Product quality", "Damaged on arrival", "Missing parts", "Packaging", "Description mismatch", "Wrong item", "Other"]


class Classification(BaseModel):
    record_id: str
    issue: Issue


class AnalysisResponse(BaseModel):
    classifications: list[Classification]
    listing_observations: list["ListingObservation"] = Field(default_factory=list, max_length=5)


class ListingObservation(BaseModel):
    issue: Issue
    evidence_ids: list[str] = Field(min_length=1, max_length=200)
    basis: Literal["listing_text", "listing_image"]
    listing_quote: str = Field(default="", max_length=500)
    observation: str = Field(min_length=1, max_length=600)


AnalysisResponse.model_rebuild()


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=9000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=1500)
    history: list[ChatMessage] = Field(default_factory=list, max_length=10)
    days: int = Field(default=28, ge=7, le=90)
    source: Literal["all", "synthetic", "csv", "shopify", "woocommerce"] = "all"


class ChatAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    points: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(min_length=1, max_length=5)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    follow_up_questions: list[str] = Field(default_factory=list, max_length=2)

    @field_validator("points")
    @classmethod
    def concise_points(cls, points):
        points=[point.strip() for point in points]
        if any(not point for point in points) or sum(len(point.split()) for point in points)>150:
            raise ValueError("Answers must contain nonempty points within 150 words.")
        return points
