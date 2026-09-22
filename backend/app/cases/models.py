"""Data contract for repair cases: shared by the HTTP layer, tests, and OpenAPI docs.

Draft, discussed in Issue #2 with Egor (frontend) and Max (AI/search) before
independent implementation. Keep `contracts/case.example.json` in sync with
this module when either changes.

Product rules encoded here (see docs/architecture.md and root AGENTS.md):
- The source message is stored separately from extracted fields and is never
  overwritten by extraction or edits.
- Unknown facts are represented as explicit `null`, never guessed or defaulted.
- `cause` is independent of `action`: it is never derived automatically from
  the action taken, only recorded when explicitly stated.
- The message author (`source.author_id`) and the repair `participant` are
  different concepts; a participant is only set when explicitly named.
- `draft` and `confirmed` are distinct states. Only `confirm` can move a case
  from `draft` to `confirmed`, and only the server assigns `confirmed_by`.
"""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CaseStatus = Literal["draft", "confirmed"]
SourceType = Literal["max_message", "manual"]


class ContractModel(BaseModel):
    """Shared strictness: an unrecognized field is a 422, never silently dropped."""

    model_config = ConfigDict(extra="forbid")


class Equipment(ContractModel):
    """Equipment mentioned in the source message. Null at case level if unclear."""

    id: str = Field(
        min_length=1, description="Stable equipment identifier, e.g. an inventory code."
    )
    label: str | None = Field(
        default=None, description="Human-readable name, if different from `id`; null if unknown."
    )


class Participant(ContractModel):
    """Person who took part in the repair. Distinct from `source.author_id`."""

    id: str = Field(min_length=1, description="Stable participant identifier.")
    display_name: str | None = Field(
        default=None, description="Display name; null if only an id is known."
    )


class ParticipantInput(ContractModel):
    """How a caller names a participant; the server resolves/assigns `id`."""

    id: str | None = Field(default=None, description="Existing participant id, if known.")
    display_name: str = Field(
        min_length=1,
        description="Name as mentioned in the source. Required to name a participant at all.",
    )


class Source(ContractModel):
    """The original message. Stored as-is, never overwritten by extraction or edits."""

    id: str = Field(
        min_length=1, description="Message id assigned by the sending adapter (e.g. MAX)."
    )
    type: SourceType
    text: str = Field(min_length=1)
    author_id: str = Field(
        min_length=1, description="Who sent the message. Not necessarily the repair participant."
    )
    received_at: datetime
    external_event_id: str | None = Field(
        default=None,
        description=(
            "Delivery id used to deduplicate redelivered events (e.g. a MAX webhook id). "
            "Null for manually entered sources, which have no redelivery to dedupe."
        ),
    )

    @field_validator("received_at")
    @classmethod
    def _require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("received_at must be UTC, e.g. '...Z' or '+00:00'")
        return value


class Case(ContractModel):
    """A repair case: extracted facts plus draft/confirmed lifecycle state."""

    id: str
    status: CaseStatus
    source: Source
    equipment: Equipment | None = None
    symptom: str | None = None
    cause: str | None = Field(
        default=None,
        description="Root cause, if stated. Never inferred automatically from `action`.",
    )
    action: str | None = None
    result: str | None = None
    participant: Participant | None = None
    confirmed_by: str | None = Field(
        default=None, description="User id who confirmed the case. Set by the server only."
    )
    confirmed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    is_demo: bool = Field(default=False, description="Synthetic/demo data, never a real report.")


class CaseCreateRequest(ContractModel):
    """Draft creation payload: sent by the MAX adapter, the AI pipeline, or manual entry."""

    source: Source
    equipment: Equipment | None = None
    symptom: str | None = None
    cause: str | None = None
    action: str | None = None
    result: str | None = None
    participant: ParticipantInput | None = None
    is_demo: bool = False


class CaseUpdateRequest(ContractModel):
    """Partial correction of a draft.

    A field left out of the request body is unchanged; a field sent as
    explicit `null` clears that fact. Only allowed while `status == draft`.
    """

    equipment: Equipment | None = None
    symptom: str | None = None
    cause: str | None = None
    action: str | None = None
    result: str | None = None
    participant: ParticipantInput | None = None


class CaseConfirmRequest(CaseUpdateRequest):
    """Optional last-moment corrections applied atomically with confirmation."""


class CaseSearchResult(ContractModel):
    case: Case
    similarity_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Normalized relevance in [0, 1], where a larger value means a closer match; "
            "null under the current keyword filter."
        ),
    )
    match_explanation: str | None = Field(
        default=None,
        description=(
            "Short factual explanation of the matched fields; null until similarity search "
            "(Issue #8) provides one."
        ),
    )


class CaseSearchResponse(ContractModel):
    items: list[CaseSearchResult]
    next_cursor: str | None = Field(
        default=None, description="Opaque pagination token; null when there is no further page."
    )


class CaseHistoryResponse(ContractModel):
    equipment_id: str
    items: list[Case]
    next_cursor: str | None = Field(
        default=None, description="Opaque pagination token; null when there is no further page."
    )


class ErrorDetail(ContractModel):
    code: str
    message: str


class ErrorResponse(ContractModel):
    error: ErrorDetail
