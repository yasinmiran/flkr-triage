from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NoteKind(Enum):
    SIBLING = "sibling"
    PARTNER_HAS_FINDING = "partner_has_finding"
    PARTNER_UNVERIFIED = "partner_unverified"
    VOTED_GROUP = "voted_group"


@dataclass(frozen=True)
class Note:
    kind: NoteKind
    text: str


@dataclass(frozen=True)
class Equipment:
    equipment_id: str
    equipment_type: str
    service_description: str
    criticality_score: int  # 1 = least critical, 10 = most critical
    reliability_score: int  # 1 = fails frequently, 10 = highly reliable.
    safety_critical_element: bool
    redundancy: str
    engineer_comment: str

    @classmethod
    def from_row(cls, row: dict) -> "Equipment":
        return cls(
            equipment_id=row["equipment_id"].strip(),
            equipment_type=row["equipment_type"].strip(),
            service_description=row["service_description"].strip(),
            criticality_score=int(row["criticality_score"]),
            reliability_score=int(row["reliability_score"]),
            safety_critical_element=row["safety_critical_element"].strip().lower() == "yes",
            redundancy=row["redundancy"].strip(),
            engineer_comment=row["engineer_comment"].strip(),
        )


@dataclass(frozen=True)
class Finding:
    finding_id: str
    reported_date: date
    equipment_id: str
    equipment_type: str
    inspection_type: str
    inspection_method: str
    finding_description: str
    reported_by: str
    reporter_role: str

    @classmethod
    def from_row(cls, row: dict) -> "Finding":
        return cls(
            finding_id=row["finding_id"].strip(),
            reported_date=date.fromisoformat(row["reported_date"].strip()),
            equipment_id=row["equipment_id"].strip(),
            equipment_type=row["equipment_type"].strip(),
            inspection_type=row["inspection_type"].strip(),
            inspection_method=row["inspection_method"].strip(),
            finding_description=row["finding_description"].strip(),
            reported_by=row["reported_by"].strip(),
            reporter_role=row["reporter_role"].strip(),
        )


@dataclass
class Case:
    finding: Finding
    equipment: Equipment
    notes: list[Note] = field(default_factory=list)


class ScoredDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: int = Field(ge=1, le=10)
    rationale: str = Field(min_length=1)


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=300)
    likelihood_of_failure: ScoredDimension
    impact_of_failure: ScoredDimension
    recommended_action: str = Field(min_length=1, max_length=300)
    confirmed_failure: bool = Field(
        description="The item has already failed to perform a function it is currently required to perform.")
    out_of_service: bool = Field(description="The item is out of service and not required to be in service.")
    protection_layer_impaired: bool = Field(
        description="A layer that prevents or mitigates a major accident is not fully available because of this finding.")
    deviation_recorded: Literal["yes", "no", "unknown"] = Field(
        description="Whether the finding states that the impairment is recorded in the impairment register or a deviation is in place.")
    evacuation_capacity_reduced: bool = Field(
        description="This finding reduces the installation's capacity to evacuate people.")
    uncertainty: Literal["low", "medium", "high"] = Field(
        description="How much the scores depend on facts the finding does not establish.")


@dataclass
class Verdict:
    assessment: Assessment | None
    violations: list[str] = field(default_factory=list)


class Ticket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: str = Field(pattern=r"^TKT-\d{4}$")
    finding_id: str
    equipment_id: str
    summary: str = Field(min_length=1, max_length=300)
    likelihood_of_failure: ScoredDimension
    impact_of_failure: ScoredDimension
    urgency: ScoredDimension
    recommended_action: str = Field(min_length=1, max_length=300)
    review_required: bool
    review_reason: str | None

    @model_validator(mode="after")
    def reason_when_required(self) -> "Ticket":
        if self.review_required and not self.review_reason:
            raise ValueError("review_reason is required when review_required is true")
        return self


class TicketFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generated_at: str
    tickets_generated: int
    tickets: list[Ticket]
