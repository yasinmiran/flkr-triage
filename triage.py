import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path

import anthropic
from anthropic.types import JSONOutputFormatParam, MessageParam, OutputConfigParam
from pydantic import BaseModel, Field, ConfigDict, ValidationError, model_validator
from typing import Literal, cast

import yaml

DEFAULT_REGISTRY = "data/equipment_registry.csv"
DEFAULT_FINDINGS = "data/inspection_findings.csv"

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
PARTNER_TAG = re.compile(r"\(([A-Z]+-\d+)\)")

ROLE = (
    "You draft triage for a duty integrity engineer on an offshore platform. "
    "Score likelihood of failure and impact of failure only; urgency is computed elsewhere. "
    "A human approves every ticket."
)


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
            raise ValueError("review_reason is requis true")
        return self


class TicketFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generated_at: str
    tickets_generated: int
    tickets: list[Ticket]


def main(argv=None):
    load_dotenv(Path(__file__).with_name(".env"))
    registry_path, findings_path, output_path = parse_args(argv)
    registry_rows = load_csv(registry_path)
    findings_rows = load_csv(findings_path)
    equipments = [Equipment.from_row(row) for row in registry_rows]
    findings = [Finding.from_row(row) for row in findings_rows]
    registry: dict[str, Equipment] = {e.equipment_id: e for e in equipments}
    findings_by_equipment: dict[str, list[Finding]] = defaultdict(list)
    cases: list[Case] = []
    for finding in findings:
        eq = registry.get(finding.equipment_id)
        if eq is None:
            sys.exit(f"error: finding {finding.finding_id} references unknown equipment {finding.equipment_id}")
        findings_by_equipment[finding.equipment_id].append(finding)
        cases.append(Case(finding=finding, equipment=eq))
    annotate_batch(cases, registry, findings_by_equipment)
    rules_path = Path(__file__).with_name("rules.yml")
    rules_text = rules_path.read_text()
    rules = yaml.safe_load(rules_text)
    client = anthropic.Anthropic()
    tickets: list[Ticket] = []
    failed = 0
    for case in cases:
        verdict = assess(client, case, rules, rules_text)
        if verdict.assessment is None:
            failed += 1
        tickets.append(build_ticket(case, verdict, rules))
    output = TicketFile(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        tickets_generated=len(tickets),
        tickets=tickets,
    )
    output_path.write_text(output.model_dump_json(indent=2) + "\n")
    print(f"wrote {len(tickets)} tickets to {output_path}" + (f", {failed} failed triage" if failed else ""))
    return 1 if failed else 0


UNSUPPORTED_SCHEMA_KEYS = {"minimum", "maximum", "minLength", "maxLength", "exclusiveMinimum", "exclusiveMaximum",
                           "title"}


def band_name(score: int, rules: dict) -> str:
    for name, (low, high) in rules["urgency_bands"].items():
        if low <= score <= high:
            return name.replace("_", " ")
    sys.exit(f"error: urgency {score} is outside every band")


def api_schema(node: object) -> object:
    if isinstance(node, dict):
        return {k: api_schema(v) for k, v in node.items() if k not in UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(node, list):
        return [api_schema(i) for i in node]
    return node


MODEL = "claude-sonnet-5-5"
CACHE_DIR = Path(__file__).with_name(".cache")
SCHEMA: dict[str, object] = cast(dict[str, object], api_schema(Assessment.model_json_schema()))


def ask_model(client: anthropic.Anthropic, system: str, messages: list[MessageParam], rules_text: str) -> str:
    key = hashlib.sha256(f"{MODEL}\n{rules_text}\n{system}\n{json.dumps(messages)}".encode()).hexdigest()
    cached = CACHE_DIR / f"{key}.json"
    if cached.exists():
        return cached.read_text()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system,
        messages=messages,
        output_config=OutputConfigParam(format=JSONOutputFormatParam(type="json_schema", schema=SCHEMA)),
    )
    text = next(block.text for block in response.content if block.type == "text")
    CACHE_DIR.mkdir(exist_ok=True)
    cached.write_text(text)
    return text


MAX_ATTEMPTS = 3


def assess(client: anthropic.Anthropic, case: Case, rules: dict, rules_text: str) -> Verdict:
    system = system_prompt(rules)
    messages: list[MessageParam] = [MessageParam(role="user", content=user_prompt(case, rules))]
    invariants_retried = False
    for _ in range(MAX_ATTEMPTS):
        text = ask_model(client, system, messages, rules_text)
        try:
            assessment = Assessment.model_validate_json(text)
        except ValidationError as error:
            messages.append(MessageParam(role="assistant", content=text))
            messages.append(MessageParam(role="user",
                                         content=f"That response failed validation:\n{error}\nReturn a corrected response."))
            continue
        violations = invariant_violations(assessment, case)
        if violations and not invariants_retried:
            invariants_retried = True
            messages.append(MessageParam(role="assistant", content=text))
            messages.append(MessageParam(role="user", content="The scores contradict the flags:\n- " + "\n- ".join(
                violations) + "\nReconsider and return a corrected response."))
            continue
        return Verdict(assessment, violations)
    return Verdict(None, ["no valid response after retries"])


def invariant_violations(a: Assessment, case: Case) -> list[str]:
    L, I = a.likelihood_of_failure.score, a.impact_of_failure.score
    found = []
    if a.confirmed_failure and a.out_of_service:
        found.append("confirmed_failure and out_of_service cannot both be true")
    if a.confirmed_failure and L < 8:
        found.append(
            f"confirmed_failure is true but likelihood is {L}; a confirmed failure sits at the top of the range")
    if a.out_of_service and L > 4:
        found.append(
            f"out_of_service is true but likelihood is {L}; an item not required to be in service cannot be likely to fail in service")
    if case.equipment.safety_critical_element and a.confirmed_failure and I < 5:
        found.append(f"confirmed failure on a safety critical element but impact is {I}")
    return found


def urgency(a: Assessment, case: Case, rules: dict) -> tuple[int, list[str]]:
    u = rules["urgency"]
    L, I = a.likelihood_of_failure.score, a.impact_of_failure.score
    base = u["weights"]["impact"] * I + u["weights"]["likelihood"] * L
    reasons = [f"derived {base:.1f} from impact {I} and likelihood {L}, impact weighted"]
    if L >= u["near_certain"]["likelihood_at_least"] and base < I:
        base = I
        reasons.append(f"near-certain failure, held at impact {I}")
    cap = u["inconsequential"]
    if I <= cap["impact_at_most"] and base > cap["urgency_at_most"]:
        base = cap["urgency_at_most"]
        reasons.append("inconsequential item, capped at next planned shutdown")
    score = int(base + 0.5)
    floor = u["sce_floor"]
    if case.equipment.safety_critical_element and I >= floor["impact_at_least"] and score < floor["urgency_at_least"]:
        score = floor["urgency_at_least"]
        reasons.append("safety critical element with high impact, this week at the latest")
    return score, reasons


def apply_overrides(score: int, reasons: list[str], a: Assessment, case: Case, rules: dict) -> tuple[int, list[str]]:
    fired = []
    eq_type = case.equipment.equipment_type
    for o in rules["overrides"]:
        if o["id"] == "protection_layer_impaired_no_deviation":
            hit = a.protection_layer_impaired and a.deviation_recorded != "yes" and eq_type in rules[
                "protection_layer_types"]
        elif o["id"] == "evacuation_below_pob":
            hit = a.evacuation_capacity_reduced and eq_type in rules["evacuation_types"]
        else:
            sys.exit(f"error: unknown override {o['id']!r} in rules")
        if hit:
            fired.append(o["id"])
            score = max(score, o["floor"])
            reasons.append(f"override {o['id']}: {o['condition']}; {o['reason']}; urgency at least {o['floor']}")
    return score, fired


def review(a: Assessment, case: Case, verdict: Verdict, fired: list[str]) -> tuple[bool, str | None]:
    reasons = []
    if case.equipment.safety_critical_element:
        reasons.append("Safety Critical Element: every ticket is checked by a human before it enters the work queue.")
    if fired:
        reasons.append("An escalation override set the urgency.")
    if verdict.violations:
        reasons.append(
            "Scores contradicted the model's own flags after a retry: " + "; ".join(verdict.violations) + ".")
    if a.uncertainty == "high":
        reasons.append("The model rated its uncertainty high.")
    if any(n.kind is NoteKind.PARTNER_HAS_FINDING for n in case.notes):
        reasons.append("The redundant partner also has an open finding; redundancy credit withdrawn.")
    if any(n.kind is NoteKind.PARTNER_UNVERIFIED for n in case.notes):
        reasons.append("The redundant partner cannot be verified.")
    return bool(reasons), " ".join(reasons) or None


def build_ticket(case: Case, verdict: Verdict, rules: dict) -> Ticket:
    f, eq = case.finding, case.equipment
    ticket_id = "TKT-" + f.finding_id.removeprefix("F-")
    a = verdict.assessment
    if a is None:
        failed = ScoredDimension(score=10, rationale="Triage failed; human assessment required.")
        return Ticket(
            ticket_id=ticket_id, finding_id=f.finding_id, equipment_id=eq.equipment_id,
            summary=f"Finding {f.finding_id} on {eq.equipment_id} ({eq.equipment_type}) could not be triaged automatically; human assessment required.",
            likelihood_of_failure=failed, impact_of_failure=failed, urgency=failed,
            recommended_action="Assign to the duty integrity engineer for manual triage.",
            review_required=True, review_reason="Automatic triage failed: " + "; ".join(verdict.violations) + ".",
        )
    urgency_score, reasons = urgency(a, case, rules)
    urgency_score, fired = apply_overrides(urgency_score, reasons, a, case, rules)
    required, reason = review(a, case, verdict, fired)
    return Ticket(
        ticket_id=ticket_id, finding_id=f.finding_id, equipment_id=eq.equipment_id,
        summary=a.summary,
        likelihood_of_failure=a.likelihood_of_failure,
        impact_of_failure=a.impact_of_failure,
        urgency=ScoredDimension(score=urgency_score,
                                rationale="; ".join(reasons) + f". Band: {band_name(urgency_score, rules)}."),
        recommended_action=a.recommended_action,
        review_required=required, review_reason=reason,
    )


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def system_prompt(rules: dict) -> str:
    sections = {"REGISTRY SCORES": rules["registry_scores"]}
    sections.update({key.upper(): value for key, value in rules["general"].items()})
    body = "\n\n".join(f"{title}\n{render(value)}" for title, value in sections.items())
    return f"{ROLE}\n\n{body}"


def user_prompt(case: Case, rules: dict) -> str:
    f, eq = case.finding, case.equipment
    section = rules["by_equipment_type"].get(eq.equipment_type)
    if section is None:
        sys.exit(f"error: no rules section for equipment type {eq.equipment_type!r}")
    notes = "\n".join(f"- {n.text}" for n in case.notes) or "- none"
    return "\n\n".join([
        "EQUIPMENT TYPE RULES\n" + render(section),
        "EQUIPMENT\n" + "\n".join([
            f"id: {eq.equipment_id}",
            f"type: {eq.equipment_type}",
            f"service: {eq.service_description}",
            f"criticality: {eq.criticality_score} of 10, where 10 is most critical",
            f"reliability: {eq.reliability_score} of 10, where 10 is highly reliable",
            f"safety critical element: {'yes' if eq.safety_critical_element else 'no'}",
            f"redundancy as written in the registry: {eq.redundancy}",
            f"engineer comment: {eq.engineer_comment}",
        ]),
        "BATCH NOTES\n" + notes,
        "FINDING\n" + "\n".join([
            f"id: {f.finding_id}",
            f"reported: {f.reported_date}",
            f"inspection type: {f.inspection_type}",
            f"inspection method: {f.inspection_method}",
            f"reported by: {f.reporter_role}",
            f"description: {f.finding_description}",
        ]),
        "Score likelihood and impact for this finding. In each rationale, quote the measurement or phrase from the finding it rests on. ",
        "Keep summary and recommended_action to at most 300 characters each.",
    ])


def inline(items: list) -> bool:
    return all(not isinstance(i, (dict, list)) for i in items) and sum(len(str(i)) for i in items) < 80


def scalar(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(scalar(i) for i in value)
    return str(value)


def render(value, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            label = key.replace("_", " ")
            if isinstance(item, dict) or (isinstance(item, list) and not inline(item)):
                lines.append(f"{pad}{label}:\n{render(item, indent + 1)}")
            else:
                lines.append(f"{pad}{label}: {scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        return "\n".join(f"{pad}- {scalar(item)}" for item in value)
    return f"{pad}{scalar(value)}"


def leading_sentences(text: str, n: int) -> str:
    return " ".join(SENTENCE_END.split(text.strip(), maxsplit=n)[:n])


def annotate_batch(
        cases: list[Case],
        registry: dict[str, Equipment],
        findings_by_equipment: dict[str, list[Finding]]
):
    for siblings in findings_by_equipment.values():
        siblings.sort(key=lambda f: f.reported_date)

    for case in cases:
        own, eq = case.finding, case.equipment
        for fi in findings_by_equipment[eq.equipment_id]:
            if fi.finding_id != own.finding_id:
                case.notes.append(sibling_note(fi))
        value = eq.redundancy.strip().lower()
        if value in ("none", "yes (bypass available)"):
            pass
        elif value.startswith("voted"):
            case.notes.append(
                Note(
                    NoteKind.VOTED_GROUP,
                    f"{eq.redundancy} group: losing one head is degradation, not defeat, but the margin is gone."
                )
            )
        elif m := PARTNER_TAG.search(eq.redundancy):
            partner = m.group(1)
            if partner not in registry:
                case.notes.append(
                    Note(
                        NoteKind.PARTNER_UNVERIFIED,
                        f"Redundant partner {partner} is not in the registry; its condition cannot be verified."
                    )
                )
            elif partner in findings_by_equipment:
                ids = ", ".join(f.finding_id for f in findings_by_equipment[partner])
                case.notes.append(
                    Note(
                        NoteKind.PARTNER_HAS_FINDING,
                        f"Redundant partner {partner} also has an open finding in this inspection period: {ids}. Do not credit redundancy."
                    )
                )
        else:
            sys.exit(f"error: unrecognised redundancy value {eq.redundancy!r} on {eq.equipment_id}")


def sibling_note(finding: Finding) -> Note:
    return Note(
        NoteKind.SIBLING,
        f"Other finding on {finding.equipment_id} in this inspection period: {finding.finding_id} ({finding.reported_date}): {leading_sentences(finding.finding_description, 2)}"
    )


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Triage inspection findings against the equipment registry.")
    parser.add_argument("registry", nargs="?", default=DEFAULT_REGISTRY, help="equipment registry CSV")
    parser.add_argument("findings", nargs="?", default=DEFAULT_FINDINGS, help="inspection findings CSV")
    parser.add_argument("output", nargs="?", default="tickets.json", help="where to write the tickets")
    args = parser.parse_args(argv)
    registry_path = Path(args.registry).expanduser().resolve()
    findings_path = Path(args.findings).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    for p in (registry_path, findings_path):
        if not p.is_file():
            sys.exit(f"error: file not found: {p}")
    return registry_path, findings_path, output_path


def load_csv(path):
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows


if __name__ == "__main__":
    sys.exit(main())
