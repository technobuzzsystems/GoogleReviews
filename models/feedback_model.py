"""
models/feedback_model.py
-------------------------
Data model / schema definition for a Feedback document stored in MongoDB.

MongoDB is schema-less, but we define a Python dataclass here to:
    1. Enforce structure at the application layer.
    2. Provide a single source of truth for field names.
    3. Make serialization / deserialization predictable and testable.

MongoDB Document Schema:
    company         (str)      : Display name of the company.
    company_id      (str)      : Unique company / QR code identifier.
    rating          (int)      : Star rating selected by the user (1–5).
    feedback        (str)      : The AI-generated feedback sentence chosen.
    created_date    (str)      : Submission date  — YYYY-MM-DD.
    created_time    (str)      : Submission time  — HH:MM:SS.
    created_at      (datetime) : Full UTC timestamp for precise ordering.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class FeedbackDocument:
    """Represents a single customer feedback submission stored in MongoDB."""

    company:    str
    company_id: str
    rating:     int
    feedback:   str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ─── Derived read-only fields ─────────────────────────────────────────────

    @property
    def created_date(self) -> str:
        """Submission date formatted as YYYY-MM-DD."""
        return self.created_at.strftime("%Y-%m-%d")

    @property
    def created_time(self) -> str:
        """Submission time formatted as HH:MM:SS."""
        return self.created_at.strftime("%H:%M:%S")

    # ─── Serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """
        Serialize this document to a plain dict for MongoDB insertion.
        Includes the derived created_date and created_time string fields.
        """
        return {
            "company":      self.company,
            "company_id":   self.company_id,
            "rating":       self.rating,
            "feedback":     self.feedback,
            "created_date": self.created_date,
            "created_time": self.created_time,
            "created_at":   self.created_at,
        }

    @staticmethod
    def from_dict(data: dict) -> "FeedbackDocument":
        """
        Reconstruct a FeedbackDocument from a raw MongoDB document dict.

        Args:
            data (dict): Raw MongoDB document.

        Returns:
            FeedbackDocument: Populated instance.
        """
        return FeedbackDocument(
            company=    data.get("company",    ""),
            company_id= data.get("company_id", ""),
            rating=     data.get("rating",     0),
            feedback=   data.get("feedback",   ""),
            created_at= data.get("created_at", datetime.now(timezone.utc)),
        )
