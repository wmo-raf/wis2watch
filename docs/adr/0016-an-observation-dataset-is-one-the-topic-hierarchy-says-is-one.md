# 16. An observation dataset is one the topic hierarchy says is one

Date: 2026-09-02

Status: Accepted

## Context

Nothing in this codebase had ever read the earth-system discipline or the data
category off a topic. Every dataset was treated identically -- silence judged
the same way, volume counted the same way, the centre verdict advanced on any
message at all. That is the right default for a discipline-neutral tool and
the wrong one for an installation whose primary job is watching whether
observations are still coming out of a region: a centre whose synops died three
days ago and whose aerodrome reports are flowing reads as healthy.

Telling the two apart needs a rule, and there were three places the rule could
have lived. An operator could mark each dataset by hand. A list of known
observation identifiers could be maintained. Or it could be read off what the
publishing centre itself already declared.

`Dataset.wmo_topic_hierarchy` already stores the full origin topic for every
dataset ever synced, and it is already indexed. A WIS2 data topic spells
`data/{policy}/{discipline}/{category}/...`, and the category is the level on
which WMO says what kind of thing is being published.

Measured against the region as the nodes themselves declare it: 36 observation
datasets across 27 centres, against 7 non-observation datasets across 4
centres.

## Decision

**An observation dataset is one whose topic's data category is
`surface-based-observations` or `space-based-observations`.** Nothing else, and
nowhere else.

**Every discipline counts.** The category is what says a publication is an
observation; the discipline above it says what the observation is of. Four
centres in the region file theirs under `climate` rather than `weather`, and a
rule pinned to `weather` would delete them from every observation figure. The
same goes for `hydrology`, `ocean`, `cryosphere` and the rest, none of which
the region publishes today and any of which it may tomorrow.

**The level is what gives a token its meaning, not the word appearing.** A
centre publishing on `data/core/surface-based-observations` -- a category
where a discipline belongs -- is not an observation by this rule. It is a
malformed topic, and reading it as an observation would be guessing at what
the centre meant.

**There is no per-dataset override.** A dataset filed under the wrong category
is a catalogue error at the centre, and this tool exists to report those rather
than to paper over them. An override would also quietly make the classification
somebody's judgement, at which point two installations watching the same centre
could disagree about what that centre publishes.

**A topic this tool cannot read is not an observation.** Answered rather than
raised on: a dataset learned from traffic may carry no readable topic at all,
and a classifier that threw would take a page down over one bad record.

**It is derived, never stored.** No new field, no ingest change, no re-sync,
and it is retroactive over all existing history -- every dataset ever synced is
classified the moment the rule exists. It also cannot drift out of step with
the topic it is derived from, which a denormalised column would.

**One classifier, in `interpretation/topics.py`.** That module already owns
reading meaning out of a WIS2 topic, has no database and no network, and is
tested against topics captured from a Global Broker. `Dataset.is_observation`
is the model's face on the same function rather than a second copy of the rule.

## Consequences

**The classification is on the surfaces where datasets are listed** -- the node
detail page, live and retired, and the Wagtail datasets listing -- so a reader
can see which of a centre's rows this installation is actually watching without
decoding a topic string themselves. It is spelled out for the datasets that are
not observations too: a blank cell reads as missing data, and this is an answer.

**The verdict work can be anchored on it.** That is the next slice, and it is
deliberately not this one: the classifier and its surfacing stand on their own
and change no judgement yet.

**A centre that re-files a dataset changes its classification**, with no action
here and no migration. That is the point of anchoring in the hierarchy rather
than in an operator's records, and it is also the only way this rule can be
wrong: it is exactly as right as the centre's own topic is.

## Not addressed here

**Counting or querying observation datasets in the database.** The rule is a
property of a row, and the region holds a few hundred rows. A SQL expression
of the same rule is a second place for it to live and a second place for it to
drift, and nothing needs one yet. The slice that anchors the centre verdict on
observation traffic will need to join rollups to these datasets, and it can
decide then what that join should look like -- with tests holding it to the
same answers this classifier gives.

**Reporting a dataset whose topic this tool cannot read.** Such a dataset is
now silently a non-observation, which is the safe answer but not a visible one.
Nothing in the region is in that state, and where it would be reported is the
drift report of ADR-0013 rather than here.

**Anything below the category.** `synop`, `metar`, `temp` and the levels under
them are not read, and the finer question -- which kind of observation stopped
-- is one the dataset rows already answer by name.
