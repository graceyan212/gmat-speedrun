# GMAT Focus Topic-Tag Taxonomy (T1 contract)

This file defines the **tag convention** that downstream tracks consume. It is a
shared, stable contract: a mastery SQL query (`notes.tags LIKE 'Quant::Arithmetic::Percents%'`)
and a coverage map both read these strings. **Do not rename a tag once shipped** —
treat additions as append-only and deletions as breaking changes.

## 1. Convention

```
Section::Topic::Subtopic
```

- Exactly three levels, `::` separated, no spaces inside a segment (use CamelCase).
- One topic tag per item, in addition to a `difficulty:*` tag and a `split:*` tag.
- Anki stores tags space-separated, so segments must never contain spaces.

### Section codes (level 1)

| Code           | GMAT Focus section       |
|----------------|--------------------------|
| `Quant`        | Quantitative Reasoning   |
| `Verbal`       | Verbal Reasoning         |
| `DataInsights` | Data Insights            |

### Auxiliary tag namespaces (orthogonal to the topic tag)

| Namespace      | Allowed values                  | Purpose |
|----------------|---------------------------------|---------|
| `difficulty::` | `easy`, `medium`, `hard`        | coarse difficulty (NOT IRT-calibrated) |
| `split::`      | `train`, `holdout`, `gold`      | leakage check (PRD §7e) |
| `type::`       | `Memory`                        | only on recall/memory cards; exam items omit it |

> Example full tag set on one note:
> `Quant::Arithmetic::Percents difficulty::medium split::train`

## 2. The 1:1 map onto `coverage_outline`

Every entry in the seed's `coverage_outline` gets exactly one tag. Nothing is added,
nothing is dropped.

### Quant — Quantitative Reasoning (Problem Solving only; no Geometry, no DS here)

| coverage_outline entry | Tag |
|---|---|
| Arithmetic: properties of integers | `Quant::Arithmetic::PropertiesOfIntegers` |
| Arithmetic: fractions and decimals | `Quant::Arithmetic::FractionsDecimals` |
| Arithmetic: percents | `Quant::Arithmetic::Percents` |
| Arithmetic: ratios and proportions | `Quant::Arithmetic::RatiosProportions` |
| Arithmetic: powers and roots | `Quant::Arithmetic::PowersRoots` |
| Arithmetic: statistics (mean, median, range, standard deviation) | `Quant::Arithmetic::Statistics` |
| Algebra: linear equations and systems | `Quant::Algebra::LinearEquations` |
| Algebra: quadratic equations and factoring | `Quant::Algebra::Quadratics` |
| Algebra: inequalities and absolute value | `Quant::Algebra::Inequalities` |
| Algebra: functions and exponents | `Quant::Algebra::FunctionsExponents` |
| Word problems: rate, work, mixtures, interest | `Quant::WordProblems::RateWorkMixtureInterest` |

### Verbal — Verbal Reasoning (Critical Reasoning + Reading Comprehension; no Sentence Correction)

| coverage_outline entry | Tag |
|---|---|
| Critical Reasoning: assumption | `Verbal::CriticalReasoning::Assumption` |
| Critical Reasoning: strengthen | `Verbal::CriticalReasoning::Strengthen` |
| Critical Reasoning: weaken | `Verbal::CriticalReasoning::Weaken` |
| Critical Reasoning: inference | `Verbal::CriticalReasoning::Inference` |
| Critical Reasoning: evaluate | `Verbal::CriticalReasoning::Evaluate` |
| Critical Reasoning: paradox/discrepancy | `Verbal::CriticalReasoning::Paradox` |
| Critical Reasoning: boldface/role | `Verbal::CriticalReasoning::Boldface` |
| Reading Comprehension: main idea | `Verbal::ReadingComprehension::MainIdea` |
| Reading Comprehension: detail | `Verbal::ReadingComprehension::Detail` |
| Reading Comprehension: inference | `Verbal::ReadingComprehension::Inference` |
| Reading Comprehension: function/structure | `Verbal::ReadingComprehension::Function` |
| Reading Comprehension: tone | `Verbal::ReadingComprehension::Tone` |

### DataInsights — Data Insights (DS lives HERE, not in Quant)

| coverage_outline entry | Tag |
|---|---|
| Data Sufficiency | `DataInsights::DataSufficiency` |
| Multi-Source Reasoning | `DataInsights::MultiSourceReasoning` |
| Table Analysis | `DataInsights::TableAnalysis` |
| Graphics Interpretation | `DataInsights::GraphicsInterpretation` |
| Two-Part Analysis | `DataInsights::TwoPartAnalysis` |

> Data Insights subtopics are two-level conceptually, but to keep the convention
> uniform at `Section::Topic::Subtopic` we treat the question type as the Topic and
> leave the Subtopic implicit. For SQL/coverage purposes match on the two-segment
> prefix, e.g. `notes.tags LIKE 'DataInsights::DataSufficiency%'`.

## 3. Mastery / coverage query contract

- **Per-topic mastery:** `... WHERE notes.tags LIKE '<Tag>%'` (the trailing `%`
  catches the space-delimited difficulty/split tags Anki appends).
- **Per-section coverage:** match the level-1 prefix, e.g. `'Quant::%'`.
- **Leakage check (PRD §7e):** holdout/gold items carry `split::holdout` / `split::gold`
  and must never share a stem with a `split::train` item.

## 4. Stability rules

1. Tags are **append-only**. New Focus topics get a new row here, never a rename.
2. Casing and `::` separators are frozen.
3. Section codes (`Quant`, `Verbal`, `DataInsights`) are frozen.
4. `difficulty` is coarse on purpose — consumers must report a wide readiness range.
