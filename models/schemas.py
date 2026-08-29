from pydantic import BaseModel, Field

class GenerateFeedbackRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="Star rating from 1 to 5")
    business_id: str = Field(default="technobuzz")

class SubmitFeedbackRequest(BaseModel):
    company: str = Field(..., min_length=1, max_length=200)
    company_id: str = Field(..., min_length=1, max_length=100)
    rating: int = Field(..., ge=1, le=5)
    feedback: str = Field(..., min_length=1, max_length=1000)
    business_id: str = Field(default="technobuzz")


class GenerateExamplesRequest(BaseModel):
    name: str = Field(default="", max_length=200)
    scope: str = Field(..., min_length=8, max_length=4000)
