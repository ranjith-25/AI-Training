# Search-only dump — all 8 questions × both chunking strategies

Retrieval: MongoDB Atlas `$vectorSearch`, exact ENN, cosine similarity.
**Scores are cosine similarity in [0,1] — higher is better.**

Legend: `**` = form AND clause match (strict hit) · `~` = form matches only · blank = neither

## Q1 — Under HO-0304 ed. 03-24, does exclusion E-17 apply to water damage caused by a burst interior supply line?

- **Expected form:** `HO-0304`
- **Expected clause:** SECTION IV — EXCLUSIONS TABLE, E-17

### `naive_fixed` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ** | 1 | `HO-0304_naive_004` | HO-0304 | Homeowners |  | 0.8953 | Y | Y |
| ** | 2 | `HO-0304_naive_003` | HO-0304 | Homeowners |  | 0.8816 | Y | Y |
| ~ | 3 | `HO-0304_naive_002` | HO-0304 | Homeowners |  | 0.8767 | Y | n |
| ~ | 4 | `HO-0304_naive_000` | HO-0304 | Homeowners |  | 0.8739 | Y | n |
| ~ | 5 | `HO-0304_naive_001` | HO-0304 | Homeowners |  | 0.8715 | Y | n |

### `structure_aware` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ** | 1 | `HO-0304_struct_003` | HO-0304 | Homeowners | EXCLUSIONS TABLE | 0.8920 | Y | Y |
| ~ | 2 | `HO-0304_struct_002` | HO-0304 | Homeowners | COVERAGE MODIFICATIONS | 0.8766 | Y | n |
| ~ | 3 | `HO-0304_struct_000` | HO-0304 | Homeowners | SCOPE AND PURPOSE | 0.8720 | Y | n |
| ~ | 4 | `HO-0304_struct_001` | HO-0304 | Homeowners | DEFINITIONS | 0.8680 | Y | n |
|  | 5 | `HO-0306_struct_003` | HO-0306 | Homeowners | EXCLUSIONS TABLE | 0.8584 | n | n |

## Q2 — Is water damage from a sewer backup covered under HO-0304 ed. 03-24?

- **Expected form:** `HO-0304`
- **Expected clause:** SECTION IV — EXCLUSIONS TABLE, E-18

### `naive_fixed` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ** | 1 | `HO-0304_naive_004` | HO-0304 | Homeowners |  | 0.8831 | Y | Y |
| ~ | 2 | `HO-0304_naive_000` | HO-0304 | Homeowners |  | 0.8793 | Y | n |
| ~ | 3 | `HO-0304_naive_006` | HO-0304 | Homeowners |  | 0.8712 | Y | n |
| ~ | 4 | `HO-0304_naive_005` | HO-0304 | Homeowners |  | 0.8596 | Y | n |
| ~ | 5 | `HO-0304_naive_002` | HO-0304 | Homeowners |  | 0.8588 | Y | n |

### `structure_aware` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ~ | 1 | `HO-0304_struct_002` | HO-0304 | Homeowners | COVERAGE MODIFICATIONS | 0.8812 | Y | n |
| ~ | 2 | `HO-0304_struct_000` | HO-0304 | Homeowners | SCOPE AND PURPOSE | 0.8790 | Y | n |
| ** | 3 | `HO-0304_struct_003` | HO-0304 | Homeowners | EXCLUSIONS TABLE | 0.8788 | Y | Y |
| ~ | 4 | `HO-0304_struct_001` | HO-0304 | Homeowners | DEFINITIONS | 0.8740 | Y | n |
| ~ | 5 | `HO-0304_struct_004` | HO-0304 | Homeowners | CONDITIONS | 0.8649 | Y | n |

## Q3 — Does HO-0308 ed. 03-24 cover an air-conditioning compressor that fails due to normal wear and tear?

- **Expected form:** `HO-0308`
- **Expected clause:** SECTION IV — EXCLUSIONS TABLE, E-27

### `naive_fixed` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ~ | 1 | `HO-0308_naive_005` | HO-0308 | Dwelling Fire |  | 0.8720 | Y | n |
| ~ | 2 | `HO-0308_naive_000` | HO-0308 | Dwelling Fire |  | 0.8703 | Y | n |
| ** | 3 | `HO-0308_naive_002` | HO-0308 | Dwelling Fire |  | 0.8556 | Y | Y |
| ~ | 4 | `HO-0308_naive_001` | HO-0308 | Dwelling Fire |  | 0.8470 | Y | n |
| ~ | 5 | `HO-0308_naive_003` | HO-0308 | Dwelling Fire |  | 0.8375 | Y | n |

### `structure_aware` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ~ | 1 | `HO-0308_struct_000` | HO-0308 | Dwelling Fire | SCOPE AND PURPOSE | 0.8676 | Y | n |
| ** | 2 | `HO-0308_struct_003` | HO-0308 | Dwelling Fire | EXCLUSIONS TABLE | 0.8604 | Y | Y |
| ~ | 3 | `HO-0308_struct_001` | HO-0308 | Dwelling Fire | DEFINITIONS | 0.8586 | Y | n |
| ~ | 4 | `HO-0308_struct_002` | HO-0308 | Dwelling Fire | COVERAGE MODIFICATIONS | 0.8582 | Y | n |
| ~ | 5 | `HO-0308_struct_004` | HO-0308 | Dwelling Fire | CONDITIONS | 0.8563 | Y | n |

## Q4 — What is the effective date of the Earth Movement endorsement HO-0306 ed. 03-24?

- **Expected form:** `HO-0306`
- **Expected clause:** Header — Effective Date

### `naive_fixed` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ** | 1 | `HO-0306_naive_000` | HO-0306 | Homeowners |  | 0.9147 | Y | Y |
| ~ | 2 | `HO-0306_naive_005` | HO-0306 | Homeowners |  | 0.8906 | Y | n |
| ~ | 3 | `HO-0306_naive_001` | HO-0306 | Homeowners |  | 0.8573 | Y | n |
|  | 4 | `HO-0304_naive_006` | HO-0304 | Homeowners |  | 0.8532 | n | n |
|  | 5 | `HO-0305_naive_000` | HO-0305 | Homeowners |  | 0.8521 | n | Y |

### `structure_aware` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ** | 1 | `HO-0306_struct_000` | HO-0306 | Homeowners | SCOPE AND PURPOSE | 0.9169 | Y | Y |
| ** | 2 | `HO-0306_struct_002` | HO-0306 | Homeowners | COVERAGE MODIFICATIONS | 0.9059 | Y | Y |
| ** | 3 | `HO-0306_struct_001` | HO-0306 | Homeowners | DEFINITIONS | 0.8980 | Y | Y |
| ** | 4 | `HO-0306_struct_004` | HO-0306 | Homeowners | CONDITIONS | 0.8952 | Y | Y |
| ** | 5 | `HO-0306_struct_003` | HO-0306 | Homeowners | EXCLUSIONS TABLE | 0.8728 | Y | Y |

## Q5 — Which endorsements in the index apply to the Dwelling Fire policy line?

- **Expected form:** `HO-0308,HO-0309`
- **Expected clause:** Header — Policy Line

### `naive_fixed` — strict: MISS · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
|  | 1 | `HO-0305_naive_002` | HO-0305 | Homeowners |  | 0.8537 | n | n |
|  | 2 | `HO-0305_naive_000` | HO-0305 | Homeowners |  | 0.8463 | n | Y |
|  | 3 | `HO-0306_naive_002` | HO-0306 | Homeowners |  | 0.8460 | n | n |
| ~ | 4 | `HO-0309_naive_003` | HO-0309 | Dwelling Fire |  | 0.8454 | Y | n |
|  | 5 | `HO-0306_naive_003` | HO-0306 | Homeowners |  | 0.8441 | n | n |

### `structure_aware` — strict: MISS · loose: MISS

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
|  | 1 | `HO-0305_struct_002` | HO-0305 | Homeowners | COVERAGE MODIFICATIONS | 0.8460 | n | Y |
|  | 2 | `HO-0304_struct_002` | HO-0304 | Homeowners | COVERAGE MODIFICATIONS | 0.8452 | n | Y |
|  | 3 | `HO-0305_struct_000` | HO-0305 | Homeowners | SCOPE AND PURPOSE | 0.8442 | n | Y |
|  | 4 | `HO-0306_struct_002` | HO-0306 | Homeowners | COVERAGE MODIFICATIONS | 0.8438 | n | Y |
|  | 5 | `HO-0304_struct_000` | HO-0304 | Homeowners | SCOPE AND PURPOSE | 0.8427 | n | Y |

## Q6 — Under HO-0309 ed. 03-24, is the mysterious disappearance of an unscheduled ring covered?

- **Expected form:** `HO-0309`
- **Expected clause:** SECTION IV — EXCLUSIONS TABLE, E-30

### `naive_fixed` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ~ | 1 | `HO-0309_naive_006` | HO-0309 | Dwelling Fire |  | 0.8760 | Y | n |
| ** | 2 | `HO-0309_naive_003` | HO-0309 | Dwelling Fire |  | 0.8738 | Y | Y |
| ** | 3 | `HO-0309_naive_002` | HO-0309 | Dwelling Fire |  | 0.8730 | Y | Y |
| ~ | 4 | `HO-0309_naive_001` | HO-0309 | Dwelling Fire |  | 0.8661 | Y | n |
| ~ | 5 | `HO-0309_naive_000` | HO-0309 | Dwelling Fire |  | 0.8631 | Y | n |

### `structure_aware` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ** | 1 | `HO-0309_struct_003` | HO-0309 | Dwelling Fire | EXCLUSIONS TABLE | 0.8852 | Y | Y |
| ~ | 2 | `HO-0309_struct_001` | HO-0309 | Dwelling Fire | DEFINITIONS | 0.8764 | Y | n |
| ~ | 3 | `HO-0309_struct_002` | HO-0309 | Dwelling Fire | COVERAGE MODIFICATIONS | 0.8699 | Y | n |
| ~ | 4 | `HO-0309_struct_000` | HO-0309 | Dwelling Fire | SCOPE AND PURPOSE | 0.8689 | Y | n |
| ~ | 5 | `HO-0309_struct_004` | HO-0309 | Dwelling Fire | CONDITIONS | 0.8667 | Y | n |

## Q7 — What coverage does HO-0305 ed. 03-24 provide?

- **Expected form:** `HO-0305`
- **Expected clause:** SECTION I — SCOPE AND PURPOSE

### `naive_fixed` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ** | 1 | `HO-0305_naive_000` | HO-0305 | Homeowners |  | 0.8650 | Y | Y |
| ~ | 2 | `HO-0305_naive_005` | HO-0305 | Homeowners |  | 0.8506 | Y | n |
|  | 3 | `HO-0304_naive_006` | HO-0304 | Homeowners |  | 0.8501 | n | n |
|  | 4 | `HO-0306_naive_000` | HO-0306 | Homeowners |  | 0.8474 | n | Y |
|  | 5 | `HO-0304_naive_000` | HO-0304 | Homeowners |  | 0.8416 | n | Y |

### `structure_aware` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ** | 1 | `HO-0305_struct_000` | HO-0305 | Homeowners | SCOPE AND PURPOSE | 0.8612 | Y | Y |
| ** | 2 | `HO-0305_struct_002` | HO-0305 | Homeowners | COVERAGE MODIFICATIONS | 0.8552 | Y | Y |
|  | 3 | `HO-0306_struct_002` | HO-0306 | Homeowners | COVERAGE MODIFICATIONS | 0.8468 | n | Y |
| ~ | 4 | `HO-0305_struct_004` | HO-0305 | Homeowners | CONDITIONS | 0.8467 | Y | n |
|  | 5 | `HO-0304_struct_002` | HO-0304 | Homeowners | COVERAGE MODIFICATIONS | 0.8455 | n | Y |

## Q8 — How many days of continuous leakage triggers the gradual seepage exclusion E-15 under HO-0304 ed. 03-24?

- **Expected form:** `HO-0304`
- **Expected clause:** SECTION IV — EXCLUSIONS TABLE, E-15

### `naive_fixed` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ** | 1 | `HO-0304_naive_002` | HO-0304 | Homeowners |  | 0.8964 | Y | Y |
| ~ | 2 | `HO-0304_naive_003` | HO-0304 | Homeowners |  | 0.8593 | Y | n |
| ~ | 3 | `HO-0304_naive_001` | HO-0304 | Homeowners |  | 0.8508 | Y | n |
| ~ | 4 | `HO-0304_naive_004` | HO-0304 | Homeowners |  | 0.8483 | Y | n |
| ~ | 5 | `HO-0304_naive_000` | HO-0304 | Homeowners |  | 0.8429 | Y | n |

### `structure_aware` — strict: HIT · loose: HIT

| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |
|---|---|---|---|---|---|---|---|---|
| ** | 1 | `HO-0304_struct_003` | HO-0304 | Homeowners | EXCLUSIONS TABLE | 0.8846 | Y | Y |
| ~ | 2 | `HO-0304_struct_001` | HO-0304 | Homeowners | DEFINITIONS | 0.8577 | Y | n |
| ~ | 3 | `HO-0304_struct_002` | HO-0304 | Homeowners | COVERAGE MODIFICATIONS | 0.8463 | Y | n |
|  | 4 | `HO-0306_struct_003` | HO-0306 | Homeowners | EXCLUSIONS TABLE | 0.8437 | n | n |
| ~ | 5 | `HO-0304_struct_000` | HO-0304 | Homeowners | SCOPE AND PURPOSE | 0.8418 | Y | n |
