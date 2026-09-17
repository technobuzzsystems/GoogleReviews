from typing import Literal

from pydantic import BaseModel, Field

ReviewLanguage = Literal["en", "mr", "hi", "gu", "ta", "te", "kn", "bn", "pa", "ml"]

class GenerateFeedbackRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="Star rating from 1 to 5")
    business_id: str = Field(default="technobuzz")
    language: ReviewLanguage = Field(default="en", description="Language for generated review text")

class SubmitFeedbackRequest(BaseModel):
    company: str = Field(..., min_length=1, max_length=200)
    company_id: str = Field(..., min_length=1, max_length=100)
    rating: int = Field(..., ge=1, le=5)
    feedback: str = Field(..., min_length=1, max_length=1000)
    business_id: str = Field(default="technobuzz")


class GenerateExamplesRequest(BaseModel):
    name: str = Field(default="", max_length=200)
    scope: str = Field(..., min_length=8, max_length=4000)


class GenerateReplyRequest(BaseModel):
    review_text: str = Field(..., min_length=1, max_length=4000, description="Customer review text")
    reviewer_name: str = Field(default="", max_length=200, description="Name of the customer reviewer")
    rating: int = Field(default=5, ge=1, le=5, description="Star rating from 1 to 5")
    business_id: str = Field(default="technobuzz", description="Business key/id")
    business_name: str = Field(default="", max_length=200, description="Organization/Business name from Google")
    language: str = Field(default="auto", description="Language code (auto, mr, en, hi, etc.)")
    tone: str = Field(default="professional_warm", description="Tone (professional_warm, casual_friendly, formal, short)")
    signature: str = Field(default="", max_length=200, description="Optional custom business signature")
    auto_sign: bool = Field(default=True, description="Whether to append business name/signature")



class BatchGenerateReplyRequest(BaseModel):
    reviews: list[GenerateReplyRequest]


class LogReplyRequest(BaseModel):
    business_id: str = Field(default="technobuzz")
    reviewer_name: str = Field(default="")
    rating: int = Field(default=5, ge=1, le=5)
    review_text: str = Field(default="")
    reply_text: str = Field(default="")
    language: str = Field(default="auto")
    tone: str = Field(default="professional_warm")
    status: str = Field(default="sent")  # generated | sent | failed
    source: str = Field(default="extension")  # extension | simulator | bookmarklet | api


class SaveReplySettingsRequest(BaseModel):
    business_id: str = Field(default="technobuzz")
    reply_tone: str = Field(default="professional_warm")
    reply_signature: str = Field(default="")
    reply_language_mode: str = Field(default="auto")
    auto_send_enabled: bool = Field(default=True)
    auto_send_delay: int = Field(default=2, ge=0, le=30)

