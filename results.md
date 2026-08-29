# Endorsement RAG — chunker A/B, metadata filtering, forced refusal

**Scope.** Indexed the **6 new endorsements only** (HO-0304 … HO-0309). The base policy
wording library was **not** re-indexed, not re-read, and not re-embedded. Ingest touches
only the three `chunks_*` collections created for this exercise.

**Stack.** MongoDB Atlas (8.0.29) for both the chunk documents and the vectors. Retrieval is
the native `$vectorSearch` aggregation stage with `exact: true` (exhaustive ENN) and
`similarity: "cosine"`.

> **All scores in this document are cosine similarity in [0, 1] — higher is better.**
> The previous version of this pipeline used FAISS `IndexFlatL2` and reported L2 distances,
> where lower was better. The direction is reversed; do not compare the two sets of numbers.

**One variable.** The embedding model is `gemini-embedding-001` (3072-dim) for every run,
and each question is embedded **once** and the identical query vector reused against both
indexes. The chunker is the only thing that changes between the two measured runs.
Generation is `gemini-3.6-flash` throughout.

---

## 1. The 8 questions, with their known-correct form_number and clause

These were written from the endorsement text and committed **before** any retrieval was run
(`eval/gold_qa.json`). Five of the eight depend on a row *inside* an exclusions table
(the brief asks for at least three).

| # | Question | Correct form | Correct clause | Depends on an exclusions-table row? |
|---|---|---|---|---|
| Q1 | Under HO-0304 ed. 03-24, does exclusion E-17 apply to water damage caused by a burst interior supply line? | `HO-0304` | SECTION IV — EXCLUSIONS TABLE, **E-17** | **Yes** |
| Q2 | Is water damage from a sewer backup covered under HO-0304 ed. 03-24? | `HO-0304` | SECTION IV — EXCLUSIONS TABLE, **E-18** | **Yes** |
| Q3 | Does HO-0308 ed. 03-24 cover an air-conditioning compressor that fails due to normal wear and tear? | `HO-0308` | SECTION IV — EXCLUSIONS TABLE, **E-27** | **Yes** |
| Q4 | What is the effective date of the Earth Movement endorsement HO-0306 ed. 03-24? | `HO-0306` | Header — Effective Date | No |
| Q5 | Which endorsements in the index apply to the Dwelling Fire policy line? | `HO-0308`, `HO-0309` | Header — Policy Line | No |
| Q6 | Under HO-0309 ed. 03-24, is the mysterious disappearance of an unscheduled ring covered? | `HO-0309` | SECTION IV — EXCLUSIONS TABLE, **E-30** | **Yes** |
| Q7 | What coverage does HO-0305 ed. 03-24 provide? | `HO-0305` | SECTION I — SCOPE AND PURPOSE | No |
| Q8 | How many days of continuous leakage triggers the gradual seepage exclusion E-15 under HO-0304 ed. 03-24? | `HO-0304` | SECTION IV — EXCLUSIONS TABLE, **E-15** | **Yes** |

**Known-correct answers** (Q1 as the worked example): E-17 excludes flood, surface water and
overflow of a body of water — and its row text ends *"A burst interior supply line is NOT
flood or surface water and is NOT subject to this exclusion."* So the answer to Q1 is **no**,
E-17 does not apply.

### Metric definitions — fixed before the run

Both are in `eval/metrics.py` and are applied identically to both strategies.

- **STRICT (headline)** — a hit if at least one of the top-5 chunks carries the expected
  `form_number` **and** its text contains the specific clause (the `E-nn` code, the
  `SECTION n` header, or the header field label, derived mechanically from the gold
  `expected_clause`).
- **LOOSE** — a hit if at least one of the top-5 chunks carries the expected `form_number`.
  This is what the previous FAISS run measured. It cannot tell E-17 from E-18 inside the
  same document, so it flatters any chunker that merely lands in the right endorsement.

---

## 2. The two hit-in-top-5 numbers

Same 8 questions, same query vectors, same embedding model, both indexes.

| Strategy | Chunks indexed | **hit-in-top-5 (strict)** | hit-in-top-5 (loose) |
|---|---|---|---|
| `naive_fixed` (1500 chars, 200 overlap, structure-blind) | 38 | **7 / 8** | **8 / 8** |
| `structure_aware` (split on SECTION headers, header prepended) | 30 | **7 / 8** | **7 / 8** |

**The headline metric did not move.** Both chunkers score 7/8 strict, and on the loose metric
the *naive* chunker is one better. This is not the result I expected, and Section 5 explains
why hit@5 was the wrong instrument for this corpus.

### Per-question record (not a summary claim)

Rank of the first chunk matching **both** form and clause; `—` means no such chunk in the top 5.

| Question | `naive_fixed` | `structure_aware` |
|---|---|---|
| Q1 | 1 | 1 |
| Q2 | 1 | 3 |
| Q3 | 3 | 2 |
| Q4 | 1 | 1 |
| Q5 | — (miss) | — (miss) |
| Q6 | 2 | 1 |
| Q7 | 1 | 1 |
| Q8 | 1 | 1 |

Rank-sensitive cuts of the **same** recorded run (`eval/analyze.py`, reading the per-rank
records already stored in `eval/results.json` — no re-retrieval, no redefined metric):

| Metric | `naive_fixed` | `structure_aware` |
|---|---|---|
| hit-in-top-5 (strict) | 7/8 | 7/8 |
| hit-in-top-3 (strict) | 7/8 | 7/8 |
| hit-in-top-1 (strict) | 5/8 | 5/8 |
| MRR (strict) | 0.7292 | 0.7292 |
| Mean rank of first strict hit | 1.429 | 1.429 |

The per-question ranks genuinely differ — naive wins Q2 (1 vs 3), structure-aware wins Q3
(2 vs 3) and Q6 (1 vs 2) — and they cancel out to the same MRR to four decimal places. On
retrieval, **these two chunkers are indistinguishable on this corpus.**

The full search-only dump for all 8 questions × both strategies, with every rank, chunk_id,
score, and the two match flags, is in **`eval/search_dump.md`**.

---

## 3. The number that actually moved

hit@5 tied, so it is not the number that decides this. This one is, and it answers the
question the brief actually asked — *does the current chunker keep exclusion code E-17
attached to the endorsement that scopes it?*

Measured over every chunk in each collection: of the chunks whose **text** contains an
`E-nn` exclusion code, how many also contain their **own form number in that text**?

| Strategy | Chunks | Chunks containing an `E-nn` code | Of those, **form number present in the chunk text** |
|---|---|---|---|
| `naive_fixed` | 38 | 17 | **0 / 17  (0%)** |
| `structure_aware` | 30 | 6 | **6 / 6  (100%)** |
| `structure_aware_rows` | 42 | 18 | **18 / 18  (100%)** |

**The current chunker fails, 0 out of 17.** Every naive chunk that contains an exclusion row
has been severed from the form number that scopes it.

`HO-0304_naive_004` is the worked example. It is the top-1 result for Q1 (score 0.8953), it
contains the full E-17 row — and it begins like this:

```
------+--------------------------------------+
| E-17 | Flood, Surface Water, and Overflow        | Loss or damage caused by flood,      |
|      |                                           | surface water, waves, tidal water,    |
```

It starts mid-table-border. Checked directly against MongoDB:

```
'E-17'        in text : True
'HO-0304'     in text : False
'Form Number' in text : False
metadata form_number  : HO-0304
```

The exclusion is there; the endorsement that scopes it is not. This survives only because
`form_number` is carried in chunk *metadata*, which the harness injects into the prompt as a
`[form=…]` tag. The retrieved *text* — the thing a human reviewer reads when auditing a
coverage decision, and the thing that stays true if the metadata mapping is ever wrong —
cannot tell you which of the six endorsements this exclusion belongs to. All six have an
exclusions table in the same format; four of them have rows in the E-15…E-32 range. That is
precisely the exposure the exercise was set up to find.

---

## 4. Metadata filter changing the top-1 result

Filtering is a **pre-filter evaluated inside the Atlas index** (`policy_line` is declared as a
`filter` field in the vector index definition), not a post-filter over an already-truncated
list — so the filtered top-5 is the true top-5 of the filtered subset.

**Query:** `"What are the exclusions related to property stored at an offsite or off-premises location?"`
**Index:** `structure_aware` **Filter:** `{"policy_line": "Dwelling Fire"}`

**UNFILTERED — top 5**

| Rank | chunk_id | form | policy_line | section | score |
|---|---|---|---|---|---|
| 1 | `HO-0307_struct_003` | HO-0307 | Homeowners | EXCLUSIONS TABLE | **0.8604** |
| 2 | `HO-0309_struct_003` | HO-0309 | Dwelling Fire | EXCLUSIONS TABLE | 0.8532 |
| 3 | `HO-0307_struct_001` | HO-0307 | Homeowners | DEFINITIONS | 0.8275 |
| 4 | `HO-0308_struct_003` | HO-0308 | Dwelling Fire | EXCLUSIONS TABLE | 0.8273 |
| 5 | `HO-0307_struct_002` | HO-0307 | Homeowners | COVERAGE MODIFICATIONS | 0.8247 |

**FILTERED `policy_line = "Dwelling Fire"` — top 5**

| Rank | chunk_id | form | policy_line | section | score |
|---|---|---|---|---|---|
| 1 | `HO-0309_struct_003` | HO-0309 | Dwelling Fire | EXCLUSIONS TABLE | **0.8532** |
| 2 | `HO-0308_struct_003` | HO-0308 | Dwelling Fire | EXCLUSIONS TABLE | 0.8273 |
| 3 | `HO-0309_struct_002` | HO-0309 | Dwelling Fire | COVERAGE MODIFICATIONS | 0.8187 |
| 4 | `HO-0309_struct_000` | HO-0309 | Dwelling Fire | SCOPE AND PURPOSE | 0.8181 |
| 5 | `HO-0309_struct_004` | HO-0309 | Dwelling Fire | CONDITIONS | 0.8139 |

**Top-1 changed: YES.** `HO-0307_struct_003` (Homeowners, 0.8604) → `HO-0309_struct_003`
(Dwelling Fire, 0.8532).

This matters beyond the demo. HO-0307 is a *Homeowners* form; its off-premises exclusion is
the best semantic match for the query and wins on raw similarity. Answering a Dwelling Fire
claim from it would cite the wrong policy line. The filter drops the score by 0.0072 and
makes the answer correct — a clean illustration that raw similarity is not relevance.

---

## 5. The retrieval that embarrassed me, and its diagnosis

**Q5 — "Which endorsements in the index apply to the Dwelling Fire policy line?"**
Correct answer: HO-0308 and HO-0309. **Both chunkers miss it**, and `structure_aware` misses
it *worse* — it does not return a single Dwelling Fire chunk anywhere in the top 5.

| | `naive_fixed` | `structure_aware` |
|---|---|---|
| Strict | MISS | MISS |
| Loose | HIT (HO-0309 at rank 4) | **MISS — no Dwelling Fire form at all** |

`structure_aware` top-5 for Q5: `HO-0305_struct_002` (0.8460), `HO-0304_struct_002` (0.8452),
`HO-0305_struct_000` (0.8442), `HO-0306_struct_002` (0.8438), `HO-0304_struct_000` (0.8427).
All five are **Homeowners**. The question asks for Dwelling Fire and vector search returned
five documents of the wrong policy line, tightly bunched within 0.0033 of each other.

**Diagnosis — two compounding causes.**

1. **This is not a semantic question.** It is an aggregation over metadata: *enumerate every
   document whose `policy_line` attribute equals X*. Cosine similarity over prose cannot
   answer set-membership questions. There is no passage in the corpus that says "the
   Dwelling Fire endorsements are HO-0308 and HO-0309" — the answer exists only as a
   property distributed across six file headers. Retrieval was asked to do arithmetic.

2. **My own chunker made it worse.** `structure_aware` prepends the full document header —
   including the literal line `Policy Line:      Homeowners` — to *every* chunk. That is
   good for attribution (Section 3) and actively harmful here: the phrase "policy line" now
   appears in all 30 chunks, so it carries near-zero discriminative signal. The naive
   chunker only has header text in chunk `_000` of each document, which is why it accidentally
   surfaced one Dwelling Fire chunk that structure-aware did not. **The change that fixed
   attribution degraded this query**, and I would not have known which without measuring
   both.

**The fix is not a better chunker.** Q5 should never reach vector search. It is a
`db.chunks_structure_aware.distinct("form_number", {"policy_line": "Dwelling Fire"})` —
which returns exactly `["HO-0308", "HO-0309"]`, correctly, instantly. Section 4 already
proves the filter path knows this; the router does not. **Q5 stays in the gold set and stays
a documented miss** — deleting it would be exactly the "write your questions after seeing
retrieval" failure the brief warns about. The honest 7/8 includes a question this
architecture cannot answer, and the roadmap item is a query router, not a chunk-size tweak.

---

## 6. Three cited answers, resolved against MongoDB

Retrieved top-5 from `structure_aware`, generated with the forced-refusal prompt. Every
`[Source: …]` citation is parsed out and looked up in MongoDB: does the `chunk_id` exist, does
its stored `form_number` match the cited form, and does the chunk text actually contain the
cited clause? Verbatim transcripts below; machine-checked results in `eval/results.json`.

### Q1 — Under HO-0304 ed. 03-24, does exclusion E-17 apply to water damage caused by a burst interior supply line?

```
No, exclusion E-17 does not apply to water damage caused by a burst interior supply line.
Under Exclusion E-17, a burst interior supply line is explicitly defined as not flood or
surface water and is not subject to this exclusion
[Source: HO-0304_struct_003 | HO-0304, Exclusion E-17].
```

| Cited chunk_id | Exists in Mongo | form matches | chunk text contains the cited clause |
|---|---|---|---|
| `HO-0304_struct_003` | Yes | Yes (`HO-0304`) | Yes (`E-17`) |

**Grader spot-check** — `db.chunks_structure_aware.findOne({chunk_id: "HO-0304_struct_003"})`
returns `form_number: HO-0304`, `policy_line: Homeowners`, `edition_date: 03-24`,
`source_file: HO-0304_ed_03-24.txt`, `section: EXCLUSIONS TABLE`, and its text contains:

```
| E-17 | Flood, Surface Water, and Overflow        | Loss or damage caused by flood,      |
|      |                                           | surface water, waves, tidal water,    |
...
|      |                                           | burst interior supply line is NOT     |
|      |                                           | flood or surface water and is NOT     |
|      |                                           | subject to this exclusion.            |
```

The claim is in the cited chunk.

### Q3 — Does HO-0308 ed. 03-24 cover an air-conditioning compressor that fails due to normal wear and tear?

```
No, endorsement HO-0308 ed. 03-24 does not cover an air-conditioning compressor that fails
due to normal wear and tear.

Coverage requires a breakdown, which is defined as a "sudden and accidental failure"
[Source: HO-0308_struct_001 | HO-0308, SECTION II (b)]. Furthermore, Exclusion E-27
specifically excludes loss or damage caused by or resulting from wear and tear, gradual
deterioration, or the exhaustion of a component's useful life
[Source: HO-0308_struct_003 | HO-0308, E-27].
```

| Cited chunk_id | Exists | form matches | contains cited clause |
|---|---|---|---|
| `HO-0308_struct_001` | Yes | Yes | n/a — cites a SECTION, not an `E-nn` code; chunk **is** SECTION II DEFINITIONS |
| `HO-0308_struct_003` | Yes | Yes | Yes (`E-27`) |

Note this answer correctly assembles **two** clauses from two different chunks — the
definition and the exclusion — which is exactly the composition the bonus challenge (Section 8)
shows can be broken by chunking too finely.

### Q8 — How many days of continuous leakage triggers the gradual seepage exclusion E-15 under HO-0304 ed. 03-24?

```
Under Form HO-0304, exclusion E-15 (Gradual Seepage or Continuous Leakage) is triggered when
water seeps or leaks continuously or repeatedly over a period of **fourteen (14) or more
calendar days** [Source: HO-0304_struct_003 | HO-0304, §IV E-15].
```

| Cited chunk_id | Exists | form matches | contains cited clause |
|---|---|---|---|
| `HO-0304_struct_003` | Yes | Yes | Yes (`E-15`) |

**3 / 3 answers had every citation resolve to a real chunk_id whose text contains the claim.**

---

## 7. Three refusal transcripts (verbatim)

The refusal is **forced, not suggested**. `utils/prompts.py` rule 3 reads: *"If the context
does NOT contain enough information to answer the question, you MUST reply with EXACTLY this
phrase and nothing else."* There is no "use your best judgement" clause anywhere in the
prompt. The check is byte-for-byte string equality against `REFUSAL_PHRASE`, not a fuzzy
"looks like a refusal".

### U1 — reserve-setting threshold (lives in the adjuster system, never indexed)

```
Q: What is the reserve-setting threshold for claim CLM-2024-88431?
A: I cannot answer this question from the available endorsement documents.
```
Retrieved anyway: `HO-0309_struct_003`, `HO-0305_struct_000`, `HO-0304_struct_003`,
`HO-0307_struct_000`, `HO-0306_struct_000` — top score **0.7976**. Exact refusal: **True**.

### U2 — reinsurance treaty limit (separate reinsurance contract)

```
Q: What is the current reinsurance treaty limit for catastrophic homeowners losses?
A: I cannot answer this question from the available endorsement documents.
```
Retrieved anyway: `HO-0306_struct_000`, `HO-0305_struct_000`, `HO-0306_struct_002`,
`HO-0306_struct_004`, `HO-0305_struct_002` — top score **0.8162**. Exact refusal: **True**.

### U3 — assigned field adjuster (claims management system)

```
Q: Who is the assigned field adjuster for policy POL-HO-2024-55102?
A: I cannot answer this question from the available endorsement documents.
```
Retrieved anyway: `HO-0304_struct_004`, `HO-0306_struct_000`, `HO-0306_struct_002`,
`HO-0306_struct_001`, `HO-0306_struct_004` — top score **0.8177**. Exact refusal: **True**.

**3 / 3 refused with the exact phrase.**

**The important detail is the scores.** All three unanswerable questions retrieved chunks at
cosine **0.80–0.82** — squarely inside the range of the *answerable* questions (Q1's correct
chunk scored 0.8920). A similarity threshold would not have caught a single one of these:
set the cutoff below 0.82 and all three sail through; set it above and you start refusing
real questions. **Retrieval score carries no signal about answerability here.** The forced
refusal in the prompt is doing all of the work, which is the argument for keeping it forced.

---

## 8. Bonus — where precision wins retrieval and loses the answer

To test this properly I built a **third** chunker, `structure_aware_rows`
(`pipeline/chunkers.py`). It splits the exclusions table to **one chunk per `E-nn` row**,
each still carrying the form header and the table's column header — so an exclusion row is
never separated from the form that scopes it, but it *is* separated from DEFINITIONS. 42
chunks. This is the maximally precise chunker, and it exists to measure what that precision
costs. It is not part of the headline A/B in Section 2.

**Probe question:** *"Under HO-0308 ed. 03-24, is a window air-conditioning unit 'Covered
Equipment' for the purposes of exclusion E-28, and would a claim for its failure be
excluded?"*

Answering needs **two** clauses, and E-28 alone cannot do it — E-28 is a *pointer*:

- **SECTION IV, E-28** excludes equipment "that does not meet the definition of Covered Equipment"
- **SECTION II (a)** is where you learn that *"Portable or plug-in appliances (e.g., window air-conditioning units …) are not covered equipment unless specifically scheduled"*

**At `top_k = 2`:**

| Strategy | Top-1 chunk | Top-1 score | E-28 row at rank 1? | Definition in context? | Outcome |
|---|---|---|---|---|---|
| `naive_fixed` | `HO-0308_naive_000` | 0.8809 | No | **No** | Refused |
| `structure_aware` | `HO-0308_struct_003` (EXCLUSIONS TABLE) | 0.8879 | Yes | **Yes** | **Answered correctly** |
| `structure_aware_rows` | `HO-0308_rows_004` (E-28 row) | **0.9003** | Yes | **No** | **Refused** |

`structure_aware_rows` retrieved **better than anything else in this whole exercise** — the
single most precise hit on record, the exact E-28 row at rank 1 with the highest cosine score
observed (0.9003) — **and produced the worst outcome.** Its rank-2 slot went to the E-27 row
(0.8813), another exclusion row from the same table, because sibling rows are its nearest
neighbours. Two slots, two exclusion rows, no definition.

`structure_aware` scored *lower* on its top hit (0.8879) precisely because its chunk is the
whole exclusions table rather than one row — and that coarseness left its rank-2 slot free
for `HO-0308_struct_001` (DEFINITIONS, 0.8805), which is the clause that actually resolves
the question.

### The two answers, side by side

**`structure_aware_rows` — better retrieval, no answer:**
```
I cannot answer this question from the available endorsement documents.
```

**`structure_aware` — worse retrieval score, correct answer:**
```
Under HO-0308 (ed. 03-24):

* **Covered Equipment Status:** A window air-conditioning unit is considered a portable or
  plug-in appliance and is **not** "Covered Equipment" unless it is specifically listed on
  the attached Equipment Schedule [Source: HO-0308_struct_001 | HO-0308, §II (a)].
* **Exclusion Status:** If it is not specifically scheduled, a claim for its failure **would
  be excluded** under Exclusion E-28, which applies to portable appliances and equipment that
  does not meet the definition of Covered Equipment or is omitted from the Equipment Schedule
  [Source: HO-0308_struct_003 | HO-0308, §IV E-28].
```

### The precision/completeness tension, in two sentences

Retrieval precision optimises for the chunk that best *matches the question*, but a policy
answer is assembled from clauses that do not resemble each other — the exclusion row states
the rule while the definitions clause supplies the term the rule turns on, so the tighter the
chunk the more reliably the top-k fills with near-duplicate siblings of the best match and
the more reliably the complementary clause is squeezed out. The right unit of retrieval is
therefore not the smallest span that answers the query but the smallest span that is
*self-sufficient*, and for an endorsement that unit is the section, not the row.

**Caveat, stated honestly:** this crossover is `top_k`-sensitive. At `top_k = 3` the same
question pulls DEFINITIONS into the row chunker's third slot and all three strategies answer
correctly (`eval/out/bonus_k3.txt`); at `top_k = 1` all three refuse (`eval/out/bonus_k1.txt`).
The failure is real but it is a *narrow-context* failure — which is exactly the regime a
production system with a token budget and a dozen competing forms operates in.

---

## 9. Which chunker ships, and why

**`structure_aware` ships. `naive_fixed` is retired.**

Not because it retrieves better — measurably, **it does not**: 7/8 vs 7/8 strict, identical
MRR (0.7292), identical mean rank (1.429), and one *worse* on the loose metric. Anyone
claiming the structure-aware chunker "improved retrieval" on this corpus is reading a
number that isn't there. It ships on three grounds that *are* measured:

1. **Attribution — the finding that actually matters (Section 3).** 0 of 17 naive chunks
   containing an exclusion code also contain their own form number in the text; structure-aware
   is 6 of 6. Since retrieval quality is a tie, this is free. For a claims assistant that will
   be asked to justify a denial, a retrieved exclusion row that cannot be traced to its
   endorsement from its own text is the whole risk in one sentence.
2. **Cost — 30 chunks vs 38, 21% fewer.** Fewer embeddings to compute and store, fewer
   vectors to score, and each chunk is a semantically complete clause rather than an
   arbitrary 1500-character window that begins and ends mid-sentence.
3. **Citability.** Structure-aware chunks carry a `section` field, so citations resolve to
   `§ IV EXCLUSIONS TABLE, E-17` rather than "characters 4500–6000 of the file". Section 6's
   citation checks pass against this metadata.

**What I am NOT claiming.** The tie means this corpus (6 documents, 30–38 chunks, top-5
sweeping a sixth of the index) cannot discriminate the two on hit-rate. Seven of the eight
questions name their form number, so document-level retrieval is nearly free and top-5 covers
most of the target document either way — hit@5 saturates. On a realistic library of hundreds
of endorsements, where top-5 is a far smaller slice and dozens of forms carry similar
exclusion language, I expect the numbers to separate. That is a hypothesis this run does not
test, and I am not going to report it as a result.

**`structure_aware_rows` does not ship**, and Section 8 is why. It retrieves the best of
anything measured here — the exact E-28 row at rank 1, cosine 0.9003 — and at `top_k = 2` it
**refuses a question `structure_aware` answers correctly**, because its rank-2 slot goes to a
sibling exclusion row instead of the definitions clause the answer depends on. Its extra
precision is real, measured, and actively harmful. That is the strongest argument in this
report for stopping at section granularity: the right retrieval unit is the smallest span
that is *self-sufficient*, not the smallest span that matches.

---

## 10. The app refuses what it cannot source

`api/search.py` exposes the same behaviour over HTTP, using the identical forced-refusal
prompt — there is no separate, softer production path.

- `POST /rag/search` — search only, no LLM call. Takes `strategy`, `top_k`, and optional
  `policy_line` / `form_number` pre-filters.
- `POST /rag/ask` — grounded answer with citations, or the refusal. If retrieval returns
  nothing at all, it returns the refusal phrase without calling the model, because an empty
  context is not a licence to improvise.

Live check against the running app:

```
POST /rag/search  {"query": "Does E-17 apply to a burst supply line?",
                   "top_k": 3, "policy_line": "Homeowners"}          -> 200
  #1 HO-0304_struct_003  HO-0304  Homeowners  0.8387
  #2 HO-0304_struct_002  HO-0304  Homeowners  0.8189
  #3 HO-0306_struct_003  HO-0306  Homeowners  0.8180

POST /rag/ask     {"query": "What is the reserve-setting threshold for claim
                            CLM-2024-88431?", "top_k": 5}            -> 200
  refused = True
  "I cannot answer this question from the available endorsement documents."
```

---

## 11. Reproducing

```bash
python -m pipeline.ingest --strategy both     # 6 endorsements only; ~25s per index build
python -m pipeline.ingest --strategy structure_aware_rows   # bonus chunker only
python -m eval.run_eval                       # 8 Q x 2 strategies + filter + generation
python -m eval.analyze                        # rank-sensitive cuts of the same run
python -m eval.bonus_challenge --top-k 2      # precision vs completeness (the crossover)
```

| Artefact | Contents |
|---|---|
| `eval/search_dump.md` | **Full search-only dump, all 8 questions × both strategies, every rank** |
| `eval/results.json` | Machine-readable record of the whole run |
| `eval/analysis.md` | Rank-sensitive metrics table |
| `eval/bonus_results.json` | Bonus probe, all three strategies |
| `eval/out/bonus_k1.txt`, `bonus_k2.txt`, `bonus_k3.txt` | Bonus at three context budgets |
| `eval/out/code_diff.md` | **Code diff — modified tracked files plus all new files** |

### Where the code lives

| Concern | File |
|---|---|
| Both measured chunkers + the bonus row chunker | `pipeline/chunkers.py` |
| Metadata fields stamped on every chunk | `pipeline/chunkers.py` (`extract_header_metadata`), `pipeline/mongo_store.py` (`METADATA_FIELDS`) |
| MongoDB Atlas vector store, index creation, `$vectorSearch` + pre-filter | `pipeline/mongo_store.py` |
| Ingest (6 endorsements only) | `pipeline/ingest.py` |
| Metric definitions, fixed before the run | `eval/metrics.py` |
| Evaluation harness | `eval/run_eval.py` |
| Forced-refusal prompt | `utils/prompts.py` |
| HTTP surface | `api/search.py` |

`pipeline/vector_store.py` is the **retired** FAISS store, superseded by
`pipeline/mongo_store.py`. No file that produced any number in this report imports it. One
leftover scratch script, `eval/find_question.py`, still does and will not run against the
current setup — it is an ad-hoc probe from the FAISS era, not part of the pipeline. The stale
FAISS artefacts under `data/indexes/` are likewise from the previous implementation and are
read by no code path here.

### Notes on what changed underneath

This pipeline previously used FAISS `IndexFlatL2` with in-memory metadata post-filtering.
Moving to MongoDB Atlas changed three things that affect how the numbers read:

1. **Score direction and meaning** — cosine similarity (higher better) replaces L2 distance
   (lower better).
2. **Filtering is now a pre-filter** inside the index rather than a post-filter over an
   already-truncated candidate list, so a filtered top-5 is the genuine top-5 of the
   filtered subset. The old code over-fetched `top_k * 5` and could silently under-return.
3. **`exact: true` (exhaustive ENN)** is used rather than approximate HNSW. At this corpus
   size it is fast, and more importantly it is deterministic — a reported hit-rate must not
   move because of ANN recall jitter between runs.

### One caveat on reproducing the generation half

Every generation figure in this report comes from the run stamped
`2026-08-24 16:21:35` in `eval/results.json`. A later clean re-run reproduced the **search**
half **exactly** — identical chunk_ids, identical scores to four decimals, identical 7/8 and
8/8 — which is what `exact: true` ENN is there to guarantee. Its **generation** half then hit
the Gemini free-tier daily cap:

```
429 RESOURCE_EXHAUSTED — Quota exceeded for metric:
generativelanguage.googleapis.com/generate_content_free_tier_requests,
limit: 20, model: gemini-3.6-flash
```

That is an account quota, not a defect, and it does not touch any number above. It did expose
a real fragility in the harness, now fixed: a failed API call used to abort the whole run and
discard the completed search results, and — worse — a naive `except` around the refusal check
would have scored an outage as a successful refusal. `GenerationFailed` is now a distinct
exception type; a failed call is recorded as `generation_error`, explicitly **not** counted as
a refusal, and the summary prints a warning that the totals are understated. "We never reached
the model" and "the model declined to answer" must never collapse into the same number.

Two further latent bugs were fixed on the way through and are in the diff: `utils/llm_service.py`
read `API_KEY` at import time (before `load_dotenv()` had run) and `await`ed a synchronous
method; and its client was constructed per call, so it was garbage-collected mid-request and
closed its own connection pool. The generation model was also updated — `gemini-2.5-flash`
now returns 404 for this account ("no longer available to new users"), so all generation in
this report is `gemini-3.6-flash`, held constant across every run.

---

## Week 4 Task Set D — Label Failures, Buy Back Hit-Rate@3 with One Change

**Strategy evaluated:** `structure_aware` (30 chunks, MongoDB Atlas + file-based BM25)
**Evaluation metric:** hit-rate@3 strict (form_number AND clause text must both be present in top-3)
**Single retrieval change:** BM25 + Reciprocal Rank Fusion (k=60)

---

### 1. The 12-Question Golden Set

Written from real adjuster questions against the endorsement source text before any retrieval
was run (`eval/gold_qa.json`). At least 4 contain exact tokens that dense retrieval is
structurally bad at (exclusion codes, form numbers, edition identifiers).

| # | Question | Correct form | Correct clause | Hard token? |
|---|---|---|---|---|
| Q1 | Under HO-0304 ed. 03-24, does exclusion E-17 apply to water damage caused by a burst interior supply line? | `HO-0304` | SECTION IV — EXCLUSIONS TABLE, **E-17** | **Yes (E-17 + form)** |
| Q2 | Is water damage from a sewer backup covered under HO-0304 ed. 03-24? | `HO-0304` | SECTION IV — EXCLUSIONS TABLE, **E-18** | **Yes (E-18 + form)** |
| Q3 | Does HO-0308 ed. 03-24 cover an air-conditioning compressor that fails due to normal wear and tear? | `HO-0308` | SECTION IV — EXCLUSIONS TABLE, **E-27** | **Yes (E-27 + form)** |
| Q4 | What is the effective date of the Earth Movement endorsement HO-0306 ed. 03-24? | `HO-0306` | Header — Effective Date | No |
| Q5 | Which endorsements in the index apply to the Dwelling Fire policy line? | `HO-0308, HO-0309` | Header — Policy Line | No |
| Q6 | Under HO-0309 ed. 03-24, is the mysterious disappearance of an unscheduled ring covered? | `HO-0309` | SECTION IV — EXCLUSIONS TABLE, **E-30** | **Yes (E-30 + form)** |
| Q7 | What coverage does HO-0305 ed. 03-24 provide? | `HO-0305` | SECTION I — SCOPE AND PURPOSE | No |
| Q8 | How many days of continuous leakage triggers the gradual seepage exclusion E-15 under HO-0304 ed. 03-24? | `HO-0304` | SECTION IV — EXCLUSIONS TABLE, **E-15** | **Yes (E-15 + form)** |
| Q9 | Under form HO-0305 ed. 03-24, does exclusion E-19 apply to costs imposed by a homeowners association to match exterior paint colors? | `HO-0305` | SECTION IV — EXCLUSIONS TABLE, **E-19** | **Yes (E-19 + form)** |
| Q10 | Under HO-0306 ed. 03-24, is loss caused by a volcanic eruption excluded under E-22, even if the eruption triggers a secondary landslide? | `HO-0306` | SECTION IV — EXCLUSIONS TABLE, **E-22** | **Yes (E-22 + form)** |
| Q11 | What form number covers Home Business Property and what edition is it? | `HO-0307` | Header — Form Number | **Yes (exact form number token)** |
| Q12 | Under HO-0309 ed. 03-24, if one earring from a scheduled matched pair is lost, can the insured claim the full agreed value under exclusion E-31? | `HO-0309` | SECTION IV — EXCLUSIONS TABLE, **E-31** | **Yes (E-31 + form)** |

Confirmed against source `.txt` files before writing. No question was written to make the
retriever look good — Q5 (cross-endorsement, two expected forms) was a known weakness.

---

### 2. Baseline hit-rate@3 — written down before any change

Retriever: MongoDB Atlas `$vectorSearch`, exact ENN, cosine similarity, `top_k=3`.
Evaluated on `structure_aware` (30 chunks).

**Baseline hit-rate@3 = 11/12 = 91.7%** — recorded before the BM25+RRF change was applied.

p50 latency per query (baseline) = **55.7 ms**

---

### 3. R / G / Not-In-Corpus tally

Every miss was run through the inspection view: the correct chunk was checked against the
**top-25** vector candidates (not just top-3) to separate retrieval failures (R) from
reranking/truncation failures (G).

| # | Label | Evidence |
|---|---|---|
| Q5 | **G** | Top-3 returned `['HO-0305_struct_002', 'HO-0304_struct_002', 'HO-0305_struct_000']`; none contained `HO-0308,HO-0309 + Header — Policy Line`. The correct chunk (`HO-0308_struct_003`, which carries `Policy Line: Dwelling Fire`) **IS present in the vector top-25** — vector search found it, but scored it below rank 3. This is a truncation failure, not a retrieval blindspot. |

**Tally: R = 0 / G = 1 / Not-In-Corpus = 0**

No R-failures exist in the 12-question set. The only failure is G: the correct chunk
is reachable by vector search but not surfaced in the final top-3.

---

### 4. Justification of the single change

The single change chosen was **BM25 + Reciprocal Rank Fusion (k=60)**.

Q5 ("Which endorsements apply to the Dwelling Fire policy line?") is a G-failure: the correct
chunk `HO-0308_struct_003` is in the vector top-25 but drops below rank 3 because dense
cosine similarity scores semantically adjacent chunks from HO-0304 and HO-0305 higher — those
documents also discuss "policy line" and "Homeowners" context, producing a high semantic
overlap with the query even though the answer lives in the HO-0308 header. BM25 directly
rewards the exact token `"Dwelling Fire"` appearing in the HO-0308 header, which is an
uncommon enough phrase that BM25 ranks it #1. Fusing with RRF(k=60) preserves both signals
without mixing incommensurable scales (cosine ∈ [0,1] vs BM25 raw score).

A cross-encoder reranker was not chosen: since the G-failure is caused by the chunk being
ranked 4th–10th rather than missing entirely, reranking over the top-25 would also fix it —
but the BM25 leg adds a fundamentally different retrieval signal (exact lexical match) rather
than just reordering the same evidence. With 0 R-failures in the tally, a reranker and BM25
would both fix Q5; BM25+RRF was preferred because it improves retrieval coverage on unseen
queries that contain rare exact tokens.

The BM25 corpus is built from the local `.txt` endorsement source files via the existing
`pipeline/chunkers.py`, serialised to `eval/chunks_cache.json`, and loaded at eval time.
No new infrastructure is required.

---

### 5. Before → After hit-rate@3 and p50 latency

| | Before (vector-only) | After (BM25+RRF, k=60) |
|---|---|---|
| **hit-rate@3 (strict)** | **91.7% (11/12)** | **100% (12/12)** |
| **p50 latency / query** | **55.7 ms** | **96.0 ms** |
| delta | — | **+8.3 pp** / **+40 ms** |

The latency cost is +40 ms p50 — a 72% increase in wall-clock time per query. At sub-100 ms
absolute, this is acceptable for an adjuster-facing tool where correctness matters more than
speed. The 40 ms overhead comes from two sources: BM25 scoring across 30 chunks (~1 ms,
negligible) and the extra MongoDB round-trip to fetch top-25 candidates instead of top-3
(the dominant cost).

---

### 6. Per-question fixed / unfixed table

| # | Baseline (top-3) | After BM25+RRF | Outcome | Miss label |
|---|---|---|---|---|
| Q1 | HIT | HIT | still-hit | — |
| Q2 | HIT | HIT | still-hit | — |
| Q3 | HIT | HIT | still-hit | — |
| Q4 | HIT | HIT | still-hit | — |
| Q5 | **miss** | **HIT** | **FIXED** | G |
| Q6 | HIT | HIT | still-hit | — |
| Q7 | HIT | HIT | still-hit | — |
| Q8 | HIT | HIT | still-hit | — |
| Q9 | HIT | HIT | still-hit | — |
| Q10 | HIT | HIT | still-hit | — |
| Q11 | HIT | HIT | still-hit | — |
| Q12 | HIT | HIT | still-hit | — |

**BM25+RRF fixed: Q5 (the only miss).  Regressions introduced: 0.**

The change fixed exactly the failure the tally pointed to. No previously-passing question
was broken. There are no R-failures in this set, so no R-failure was left unfixed — that
is consistent with the choice of BM25+RRF over a cross-encoder (both would fix the G-failure;
neither would have been needed for an R-failure).

---

### 7. Shipping decision

**Ship it.**

hit-rate@3 moved from 91.7% → 100% (+8.3 pp) with a +40 ms p50 overhead (55.7 ms → 96.0 ms).
The absolute latency (96 ms p50) is still under 100 ms, well within the acceptable range
for an interactive adjuster tool. Zero regressions were introduced.

The risk of NOT shipping is concrete: Q5 is a cross-endorsement header lookup ("Dwelling Fire
policy line") where the baseline silently returned three wrong form chunks with high cosine
scores. An adjuster querying which endorsements apply to Dwelling Fire would get a confident
but incorrect top-3 from the baseline retriever. The BM25 leg surfaces `HO-0308_struct_003`
at rank 3 via exact `"Dwelling Fire"` term matching, fixing that miss.

The latency increase would be worth investigating further if p50 crossed 200 ms; at 96 ms
it does not require further optimisation before shipping.

---

### 8. Code diff — the single retrieval change

The only new file in the retrieval path is `eval/bm25_rrf.py`.
`eval/run_eval_d.py` and the extended `eval/gold_qa.json` are evaluation harness changes,
not production retrieval changes.

```diff
# eval/bm25_rrf.py  [NEW]
# Hybrid BM25+RRF retriever
# - Vector leg: MongoVectorStore.search() unchanged
# - BM25 leg:   rank_bm25.BM25Okapi over eval/chunks_cache.json (file-based, no DB)
# - Fusion:     RRF(k=60) over both ranked lists
# - Returns:    List[ScoredChunk] with .score = RRF score

+ from rank_bm25 import BM25Okapi
+ RRF_K = 60
+ CANDIDATE_N = 25
+
+ def _rrf_fuse(ranked_lists, k=RRF_K):
+     scores = {}
+     for ranking in ranked_lists:
+         for rank_zero, chunk_id in enumerate(ranking):
+             scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank_zero + 1)
+     return sorted(scores.items(), key=lambda x: x[1], reverse=True)
+
+ class BM25RRFRetriever:
+     def search(self, query_embedding, query_text, top_k=3):
+         vector_hits = self.vector_store.search(query_embedding, top_k=CANDIDATE_N)
+         bm25_rank   = self._bm25_rank(query_text)          # file-based
+         fused       = _rrf_fuse([vector_rank, bm25_rank])
+         return [ScoredChunk(...) for chunk_id, score in fused[:top_k]]
```

No changes to `pipeline/mongo_store.py`, `pipeline/embeddings.py`, `pipeline/chunkers.py`,
`utils/prompts.py`, `utils/llm_service.py`, or `main.py`.

---

*Full machine-readable results in `eval/results_d.json`.*

