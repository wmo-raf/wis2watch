# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those
roles to the actual label strings in this repo's issue tracker.

**Three of the five exist here. Two do not.** The table is the tracker as it
stands, not as the skills assume it — a role with no label is recorded as
having none rather than pointed at a near-enough substitute, because the
substitute would quietly retriage the issue into a different queue.

| Role in the skills | Label in our tracker | Meaning                                  |
| ------------------ | -------------------- | ---------------------------------------- |
| `needs-triage`     | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`       | **none**             | Waiting on reporter for more information |
| `ready-for-agent`  | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`  | **none**             | Requires human implementation            |
| `wontfix`          | `wontfix`            | Will not be actioned                     |

When a skill mentions a role, use the label string from this table.

## When the role has no label

Do not improvise one. `question` and `help wanted` exist and read as though
they would serve, but they are general-purpose labels a human applies for
their own reasons; borrowing them for triage makes the tracker's two
vocabularies indistinguishable after the fact.

Say in a comment what you would have applied, and leave the issue's labels as
they are. An unlabelled state a maintainer can still read is worth more than a
label that misfiles the issue.

To make one of the missing roles real, create the label and then move it into
the table above — the mapping is only true while it describes the tracker:

```sh
gh label create ready-for-human --description "Requires human implementation" --color 0e8a16
gh label create needs-info --description "Waiting on reporter for more information" --color d876e3
```

## One sharp edge

`gh issue edit` applies `--remove-label` before it validates `--add-label`, so
a single call naming a label that does not exist strips the old one and then
fails. The issue is left with neither. Add first and remove second, in two
calls, if the result matters.
