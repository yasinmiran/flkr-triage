# Inspection finding triage

Reads an equipment registry and a batch of inspection findings, asks a
model to score likelihood and impact for each finding, derives urgency in
code, and writes one ticket per finding to `tickets.json`. A human
approves every ticket; this produces the draft.

## Run

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # put your ANTHROPIC_API_KEY in it
.venv/bin/python triage.py
```

Defaults: `data/equipment_registry.csv`, `data/inspection_findings.csv`,
output `tickets.json`. All three can be passed as positional arguments.

Exit code 1 means at least one finding could not be triaged; its ticket
is still written, scored 10 across and flagged for review.

## Cost

One call per finding, about 2,400 input and 1,400 output tokens on
`claude-sonnet-5-5`. A full run of 21 findings is well under a dollar.
Raw replies are cached in `.cache/` keyed on the model, the rules file
and the prompt, so a rerun with nothing changed costs nothing.

## Files

- `triage.py`: main and arguments.
- `steps.py`: the seven steps, top to bottom in run order.
- `models.py`: the input, model-reply and ticket shapes.
- `loaders.py`: csv, `.env` and rules file.
- `rules.yml`: the handover notes and registry comments as one file an
  integrity engineer edits. Every bullet names its source.
- `DESIGN.md`: decisions in the order they were made, and the answers to
  the brief's four questions.
- `tickets.json`: the output for the supplied data, generated, not edited.
