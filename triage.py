import argparse
import csv
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Literal

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
    score: int = Field(ge=1, le=10)
    rationale: str = Field(min_length=1)


class Assessment(BaseModel):
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


def main(argv=None):
    registry_path, findings_path = parse_args(argv)
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
    rules = load_rules(Path(__file__).with_name("rules.yml"))
    print(user_prompt(cases[2], rules))
    return 0


def load_rules(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


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
        "Score likelihood and impact for this finding. In each rationale, quote the measurement or phrase from the finding it rests on.",
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
    args = parser.parse_args(argv)
    registry_path = Path(args.registry).expanduser().resolve()
    findings_path = Path(args.findings).expanduser().resolve()
    for p in (registry_path, findings_path):
        if not p.is_file():
            sys.exit(f"error: file not found: {p}")
    return registry_path, findings_path


def load_csv(path):
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows


if __name__ == "__main__":
    sys.exit(main())
