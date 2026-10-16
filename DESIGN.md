i took a bit of time to understand the assignment
then I split the assignment into steps that i can digest.

**dissected steps:**
- load, type, and join the dataset
- batch notes
- rules file
- actual model call
- urgency
- review flag
- write the tickets

**decisions log**

1. i started this task on 15th of oct. 2026 (see my commit history)

2. i want to keep the entire code simple, easy to understand.
so i can run it in one command given the two `.csv` files
and an optional path to write the tickets.

3. join is a `dict[str, Equipment]` (key: `equipment_id`) lookup.
if i dont find equipment for a finding, program exits. i didnt
drop or guess in that scenario. a missing equipment row is a
data problem, so i want the run to stop and someone to look
at the data.

4. Equipment and Finding are frozen dataclasses. has from_row
factory.

5. Case is the unit of work i use. it has a list of batch notes.
also, an item with two findings record two cases but share the same
equipment. at the end i know have 18 equipment objects, 21 cases,
21 tickets.

6. i use two flat dictionaries. static `registry: dict[str, Equipment]`
per batch `findings_by_equipment: dict[str, list[Finding]]` to keep it clea
i didnt mix a static thing with a per-run thing. thats why two containers
to hold them.

7. facts are found first by code. i want things to be deterministic.
`annotate_batch` writes short notes onto each case: siblings, a partner
that also has a finding, a partner not in the registry, voted 2oo3 groups.
all dict lookups plus one regex on `redundancy`. the model sees one
finding at a time, so it cant find these itself.

8. sibling notes carry id, date and first two sentences, not the full
text. SC-1001 has a crack and a coating backlog and the notes say never
batch those. full text would leak severity across. each ticket is scored
on its own evidence.

9. notes say "in this inspection period" so the model knows the sibling
is from the same file, not last year or from another batch.

10. notes are typed, `Note(kind, text)`. prompt joins the texts. the
review rule later checks the kind without parsing strings.

11. partner check reads `redundancy` only. `N+1 (X)` and `Duplicated (X)`
both mean partner X. then two lookups: X in `registry` (else unverified),
X in `findings_by_equipment` (then no redundancy credit).

12. voted groups get a rule, not a lookup. the other heads of FG-0455
are not registry rows and the finding already says they responded.

13. unknown `redundancy` values exit the program. a silent fall-through
would score the item as if it had no backup.

14. `rules.yml` was drafted with an LLM from `domain_knowledge.md` and
the registry `engineer_comment` column, source noted per bullet. i check
every bullet against the source and cut what i cant trace. no bullet is
copied from the findings file; the one line added because of what the
first run did with F-1016 says so in its comment (point 26).

15. i checked the sdk in the venv (`anthropic==1.15.0`). no `temperature`
param anymore, so i cant claim temp 0. structured output is
`output_config` with a json schema. `messages.parse` exists but i dont
use it, i validate and retry myself. model id is one constant and part
of the cache key.

16. six flags, each one has a consumer in code. `confirmed_failure` and
`out_of_service` -> invariants. `protection_layer_impaired`,
`deviation_recorded`, `evacuation_capacity_reduced` -> overrides.
`uncertainty` -> review. i dropped "trend still moving" and "outside
acceptance", nothing read them.

17. `confirmed_failure` means it already failed a function it is
currently required to perform. that settles F-1014: the test separator
is out of service, so not a confirmed failure, likelihood capped at 4.

18. `deviation_recorded: unknown` counts as not recorded, so the override
fires. only F-1020 says it either way. the example ticket fires the rule
on F-1005 without saying, so unknown is fail-safe. POB is not given, so a
lifeboat declared unavailable with an unverifiable partner counts as
capacity reduced.

19. cache key is sha256 of model id + rules file text + the full message
list. edit the rules and every answer is recomputed, nothing stale. the
cost is a rerun when i touch code-only numbers. i measure determinism
with a cleared-cache rerun, i dont claim it.

20. 300 char limits are pydantic, not truncation. an overrun is a
validation error and gets retried. first F-1003 call came back 318 and
309 chars, pydantic rejected both. with the limit also stated in the
prompt it came back 253 and 242.

21. the api json_schema mode rejects min/max keywords. so the schema i
send is the pydantic schema with those stripped (`api_schema`), and
pydantic with the bounds checks the reply. api enforces shape, code
enforces constraints.

22. the model returns a thinking block first. i read the block with
`type == "text"`, not `content[0]`.

23. drift. F-1003 scored likelihood 9 then 8 on two calls hours apart,
both rationales fine. thats loss-of-confidence #3 from the notes. inside
a run the cache prevents it. across runs i measure it (see D).

24. retry keeps the conversation. on a validation error i append the bad
reply and the error, so the model sees exactly what was wrong. three
attempts, then `None` -> fail-closed ticket.

25. first full run, 21 cases, all valid, one retry. the traps landed:
both firewater and both transmitter tickets name the partner finding,
F-1014 is out of service not a confirmed failure, F-1005 impact is in
the 5 to 8 band for a voted group.

26. one systematic miss: cosmetic findings on SCE items scored impact
from the equipment, not the finding. F-1016 (illegible PSV tag, in date)
got impact 8, rationale literally "impact rests on what the valve does,
not the finding wording". F-1009 (coating) got 7. fix: one `scope` line
under `general.impact` in `rules.yml`. rerun: 8 -> 3 and 7 -> 3, nothing
else touched. thats question B answered by doing it.

27. invariants: confirmed failure -> L >= 8. out of service -> L <= 4.
both true is a contradiction. SCE + confirmed failure -> I >= 5. sent
back once with the contradiction quoted. if it persists i keep the
scores and route to review. first real use: F-1014 came back L5 with
out_of_service true, the retry gave L2.

28. urgency numbers live in `rules.yml`: weights 0.7 impact 0.3
likelihood, near-certain floor (L >= 9 -> at least impact),
inconsequential cap (I <= 3 -> at most 4), SCE floor (I >= 7 -> at
least 7). rounding is `int(base + 0.5)` because python rounds 8.5 to 8.

29. overrides need two votes: the model flag and the equipment type on
the list in `rules.yml`. F-1008 (structure) and F-1013 (transmitter) had
the flag and did not fire. 8 fired: 7 protection layer, 1 evacuation.
F-1005 landed at L10 I7 U9, the example ticket's numbers.

30. review has a fixed set of reasons: SCE, override fired, unresolved
invariant, uncertainty high, partner has a finding, partner unverified,
urgency in the today band. two of them are `NoteKind` checks. they live
in code not `rules.yml` because most reference pipeline facts.

31. `Ticket` and `TicketFile` are the brief's output table as pydantic.
a malformed ticket raises before the file is written. fail-closed: a
finding that couldnt be triaged gets scores 10 across, routed to a
human, run exits 1.

32. quality read of the first `tickets.json`: summaries carry the
measurement, the repeat and the partner. two fixes. F-1005 summary said
"effectively 1oo2"; the notes logic (next failure defeats it) means
2oo2, so the VOTED_GROUP note now says that. F-1007 sat at urgency 9
with no review (not SCE, no override), so i added the today-band
trigger.

33. per client: everything platform specific is meant to live in
`rules.yml`. the redundancy string patterns are still code and would
move to the rules file for a second client. not generalised for one
platform and 21 findings.

34. known limit: voted group membership is not modelled. a second failed
head in the same 2oo3 group would not be detected as a defeat. a group
id column in the registry would fix it.

**A. structured output**

the api gets a json schema, shape only. pydantic checks the reply with
the bounds (1 to 10, 300 chars, enums). then invariants check the scores
against the flags.

- malformed response: pydantic error -> sent back to the model with the
  error quoted, up to 3 attempts.
- structural violation: same path, pydantic catches it. the api schema
  has `additionalProperties: false` everywhere so extra fields fail too.
- valid but incorrect score: invariants catch the ones i can write as a
  rule (confirmed failure with likelihood 3, out of service with
  likelihood 8). sent back once with the contradiction. if it persists i
  keep the score, never rewrite it, and the ticket goes to review with
  the reason. the rest is what `review_required` is for. a human
  approves every ticket.
- cannot produce a ticket: the ticket is still written. scores 10, 10,
  10, rationale "triage failed", review required, and the run exits 1.
  an untriaged finding on a platform must not vanish.

**B. domain knowledge**

`domain_knowledge.md` and the registry `engineer_comment` column became
`rules.yml`: one general section, one section per equipment type, and
the lists code needs (protection layer types, evacuation types, bands,
urgency numbers, overrides). every bullet names its source.

the code picks the section for the case's `equipment_type` (a dict
lookup) and renders it as text into the prompt, with the engineer
comment verbatim and the batch notes. no retrieval. two pages fit in
context; rag here would be theatre.

why this: two readers need the same knowledge. the model reads prose.
the code reads lists. one file both can read, and an integrity engineer
can edit yaml and cant edit python.

six months later, psv findings need scoring differently: edit the
`Pressure Safety Valve` section in `rules.yml`. no prompt, no code. only
psv prompts change. i did exactly this once already (point 26): one line
in the file, two tickets fixed, nothing else moved.

**C. urgency**

the model never outputs urgency. code does:

    base = 0.7 * impact + 0.3 * likelihood
    if likelihood >= 9:  base = max(base, impact)      near-certain failure
    if impact <= 3:      base = min(base, 4)           inconsequential item
    score = int(base + 0.5)
    if sce and impact >= 7: score = max(score, 7)     sce floor
    then overrides: floor 9

corners:
- likelihood 10, impact 1: 3.7, capped at 4. next planned shutdown.
- likelihood 1, impact 10: 7.3 -> 7. this week.
- 10 and 10: 10. 1 and 1: 1.

overrides, both from the notes, stated in the rationale when they apply:
- protection layer impaired and no deviation recorded (unknown counts as
  not recorded) and the equipment type is on the protection layer list:
  at least 9.
- evacuation capacity reduced and the type is on the evacuation list: at
  least 9.

why not the alternatives: the average gives 5.5 for 1 and 10, and the
notes say that needs this week. the maximum gives 10 for 10 and 1, and
the notes say that can wait for a shutdown. a lookup matrix would work
but nobody can read why a cell is 7. a weighted sum with two gates is ten
lines, and every number is in `rules.yml` for the engineer to tune.

**D. limits**

when it defers to a human: any of the seven review triggers (point 30).
in this batch 14 of 21 go to review: the 12 SCE items, the two level
transmitters via the partner note. 7 pass straight through.

what i didnt build, and why:
- rag or any retrieval. 2 pages fit in the prompt.
- an agent loop or tools. every step is a known step; a loop would move
  logic out of code where it can be read.
- a second model verifying the first. the invariants and the human do
  that, and a second model would drift too.
- fine-tuning. no labelled data, and the rules change by editing a file.
- a ui. the deliverable is one command and one file.
- a self-improving loop. it contradicts the whole design: a human
  approved rules file and a human approved ticket.

what i would add next: one extra call over all 21 draft tickets that
flags contradictions between them for the human. cheap, same spirit.

known limits: voted group membership (point 34). redundancy string
patterns are code (point 33). drift across runs (point 23 and the
measurement below).

**determinism, measured**

i cleared `.cache/` and reran all 21 against the committed `tickets.json`.
10 of 21 tickets moved a score, 8 of them by one point. the bigger ones:
F-1019 (loose lagging) likelihood 6 -> 3 and urgency 4 -> 2, F-1004 (CUI
below minimum) impact 8 -> 9 and urgency 8 -> 9, F-1013 urgency 8 -> 7,
F-1014 urgency 3 -> 2. band counts before: 3 at 10, 6 at 9, 6 at 8, 1 at
5, 2 at 4, 3 at 3. after: 2 at 10, 8 at 9, 4 at 8, 1 at 7, 1 at 5, 1 at
4, 2 at 3, 2 at 2. the override floors held on every protection layer
and evacuation case. no ticket crossed between "routine" and "serious";
the moves are inside those groups. this is what the notes call "the same
finding scored differently on different days", and the honest answer is:
the model drifts by about a point, the cache keeps a run stable, the
human reviews the ones that matter, and the floors and caps in code are
what keep the drift from changing a decision.

**assumptions**

- "this period" in the notes means the findings in the supplied file.
- a partner tag not in the registry (LB-4101) is unverifiable; no
  redundancy credit.
- `N+1 (X)` and `Duplicated (X)` both mean one partner, tag X.
- `Voted 2oo3` is the only voted pattern; the other heads are not
  registry rows.
- a finding on both legs of a pair withdraws redundancy credit whatever
  the severity of either finding.
- a finding whose equipment is not in the registry aborts the run.
- `deviation_recorded: unknown` is treated as not recorded.
- POB is not given; an unavailable lifeboat with an unverifiable partner
  is capacity reduced.
- temperature 0 is not available in the sdk version used.

**ai assistance**

i used an llm for two things and reviewed both.

- `rules.yml` first draft, from the notes and the registry comments.
  i checked every bullet against the source. overridden: the first
  draft's equipment type keys (`vessel`, `pump`) didnt match the
  registry strings and only covered 5 of 13 types, i rebuilt that
  section from the engineer comments. a crane bullet said "discard
  criterion", which came from the findings file not the registry, cut.
  the `scope` line under impact is mine, added after the first run.
- an agent in the loop while coding, as a pair. every function was typed
  in and read by me. overridden: it proposed `temperature=0`, which the
  sdk doesnt have; it proposed `content[0].text`, which crashes on a
  thinking block; it proposed truncating summaries to 300 chars, i made
  it a validation error with a retry instead.

no content in `tickets.json` was written or edited by hand.

35. split into four files, in run order. `models.py` (the shapes),
`loaders.py` (csv, .env, rules), `steps.py` (the seven steps top to
bottom, one function per step), `triage.py` (main and args). i did this
last, after the pipeline worked, and checked the output was byte
identical from the cache before committing.
