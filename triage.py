import argparse
import sys
from pathlib import Path

DEFAULT_REGISTRY = "equipment_registry.csv"
DEFAULT_FINDINGS = "inspection_findings.csv"


def main(argv=None):
    registry_path, findings_path = parse_args(argv)

    registry = load_csv(registry_path)
    findings = load_csv(findings_path)

    results = triage(registry, findings)
    report(results)

    return 0


def parse_args(argv):
    # setup positional arguments
    parser = argparse.ArgumentParser(description="Triage inspection findings against the equipment registry.")  # 8
    parser.add_argument("registry", nargs="?", default=DEFAULT_REGISTRY, help="equipment registry CSV")  # 9
    parser.add_argument("findings", nargs="?", default=DEFAULT_FINDINGS, help="inspection findings CSV")  # 10
    args = parser.parse_args(argv)

    # get the absolute paths
    registry_path = Path(args.registry).expanduser().resolve()  # 12
    findings_path = Path(args.findings).expanduser().resolve()

    # then go through each path and see whther the files exist.
    for p in (registry_path, findings_path):
        if not p.is_file():
            sys.exit(f"error: file not found: {p}")

    return registry_path, findings_path


def load_csv(path):
    pass


def report(results):
    pass


def triage(registry, findings):
    pass


if __name__ == "__main__":
    sys.exit(main())
