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
every bullet against the source and cut what i cant trace. nothing comes
from the findings file.
