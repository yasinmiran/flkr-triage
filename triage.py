import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DEFAULT_REGISTRY = "data/equipment_registry.csv"
DEFAULT_FINDINGS = "data/inspection_findings.csv"


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
    notes: list[str] = field(default_factory=list)


def main(argv=None):
    registry_path, findings_path = parse_args(argv)
    registry_rows = load_csv(registry_path)
    findings_rows = load_csv(findings_path)
    equipments = [Equipment.from_row(row) for row in registry_rows]
    findings = [Finding.from_row(row) for row in findings_rows]
    registry = {e.equipment_id: e for e in equipments}
    findings_by_equipment: dict[str, list[Finding]] = defaultdict(list)
    cases = []
    for f in findings:
        eq = registry.get(f.equipment_id)
        if eq is None:
            sys.exit(f"error: finding {f.finding_id} references unknown equipment {f.equipment_id}")
        findings_by_equipment[f.equipment_id].append(f)
        cases.append(Case(finding=f, equipment=eq))
    return 0


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
