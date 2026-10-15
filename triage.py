import argparse
import csv
import re
import sys
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path

DEFAULT_REGISTRY = "data/equipment_registry.csv"
DEFAULT_FINDINGS = "data/inspection_findings.csv"

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
PARTNER_TAG = re.compile(r"\(([A-Z]+-\d+)\)")


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
    annotation_batch(cases, registry, findings_by_equipment)
    return 0


def leading_sentences(text: str, n: int) -> str:
    return " ".join(SENTENCE_END.split(text.strip(), maxsplit=n)[:n])


def annotation_batch(
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
    note = f"""
    Other finding on {finding.equipment_id} in this inspection
    period: {finding.finding_id} ({finding.reported_date}): {leading_sentences(finding.finding_description, 2)}
    """
    return Note(NoteKind.SIBLING, textwrap.dedent(note).strip())


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


def report(results):
    pass


def triage(registry, findings):
    pass


if __name__ == "__main__":
    sys.exit(main())
