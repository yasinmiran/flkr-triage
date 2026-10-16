import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from loaders import load_csv, load_dotenv, load_rules
from models import Equipment, Finding, Ticket, TicketFile
from steps import annotate_batch, assess, build_cases, build_ticket

DEFAULT_REGISTRY = "data/equipment_registry.csv"
DEFAULT_FINDINGS = "data/inspection_findings.csv"


def main(argv=None):
    load_dotenv(Path(__file__).with_name(".env"))
    registry_path, findings_path, output_path = parse_args(argv)
    equipments = [Equipment.from_row(row) for row in load_csv(registry_path)]
    findings = [Finding.from_row(row) for row in load_csv(findings_path)]
    registry, findings_by_equipment, cases = build_cases(equipments, findings)
    annotate_batch(cases, registry, findings_by_equipment)
    rules, rules_text = load_rules(Path(__file__).with_name("rules.yml"))
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


if __name__ == "__main__":
    sys.exit(main())
