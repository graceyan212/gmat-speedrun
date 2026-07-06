# GMAT Focus Quant Reference — Percents, Ratios & Proportions

*Single source of truth for card generation (PRD 7f). Every fact below is
self-contained; a generated card is judged "correct" only if it agrees with
this document (and with `content/gold_set.json`). Topic tags follow
`content/taxonomy.md`.*

---

## 1. Percents (`Quant::Arithmetic::Percents`)

**Definition.** "Percent" means "per hundred." `x%` equals the fraction
`x / 100` and the decimal `x / 100`. So `15% = 15/100 = 0.15`.

**Percent OF a number.** `p% of N = (p / 100) * N`. This is a multiplication:
"of" means "times."

> Example: `15% of 80 = 0.15 * 80 = 12`.

**Finding the base ("… is p% of what?").** If `A` is `p%` of `N`, then
`N = A / (p / 100)`.

> Example: `45 is 30% of what number? N = 45 / 0.30 = 150`.

**Percent increase / decrease.** To increase a value by `p%`, multiply by
`(1 + p/100)`; to decrease by `p%`, multiply by `(1 - p/100)`.

- Increase `$200` by `25%`: `200 * 1.25 = 250`.
- Decrease `$200` by `25%`: `200 * 0.75 = 150`.

**Percent change formula.** `percent change = (new - old) / old * 100`. The
denominator is always the ORIGINAL value, never the new one.

> Example: revenue rises from `40` to `65`: `(65 - 40)/40 = 25/40 = 0.625 = 62.5%`.

**Successive percent changes do NOT add.** Apply the multipliers in sequence.
A `+20%` then `-20%` gives `1.20 * 0.80 = 0.96`, i.e. a net **4% decrease**, not
0%. This is the single most common percent trap on the exam.

**Converting a fraction to a percent.** Divide and multiply by 100:
`3/8 = 0.375 = 37.5%`. To go the other way, `62.5% = 0.625 = 5/8`.

**Percentage points vs percent.** A rate moving from `4%` to `6%` rises by
**2 percentage points**, but by `50%` in relative terms (`2/4`). The GMAT
exploits this distinction; keep the two ideas separate.

---

## 2. Ratios (`Quant::Arithmetic::RatiosProportions`)

**Meaning.** A ratio `a : b` compares two quantities by division; it fixes their
relative sizes, not their absolute sizes. `a : b` is equivalent to any
`ka : kb` for a nonzero constant `k` (e.g. `2:3 = 4:6 = 20:30`). Always reduce
to lowest terms when comparing.

**Parts-of-a-whole ("divide T in the ratio a : b").** The total number of parts
is `a + b`; each part is worth `T / (a + b)`; the shares are
`a * T/(a+b)` and `b * T/(a+b)`.

> Example: split `$120` in ratio `3 : 5`. Parts = `3 + 5 = 8`; each part =
> `120/8 = 15`; shares are `3*15 = 45` and `5*15 = 75`. The larger share is `$75`.

**Chaining ratios (`a:b` and `b:c` → `a:c`).** Scale each ratio so the common
term `b` matches, then read off the ends. If `a:b = 2:3` and `b:c = 4:5`, scale
`b` to a common `12`: `a:b = 8:12` and `b:c = 12:15`, so `a:c = 8:15`.

**Ratio vs actual count.** A ratio alone never tells you the actual quantities;
you also need a total or one actual value. `3:5` could mean 3 and 5, or 30 and
50. Treating the ratio terms as the counts is a classic error.

---

## 3. Proportions (`Quant::Arithmetic::RatiosProportions`)

**Definition.** A proportion is an equation of two equal ratios: `a/b = c/d`.

**Cross-multiplication.** `a/b = c/d` if and only if `a*d = b*c`. Use this to
solve for an unknown term.

> Example: `3/4 = x/20 → 3*20 = 4*x → x = 15`.

**Direct proportion.** If `y` is directly proportional to `x` (`y = kx`), then
doubling `x` doubles `y`; the ratio `y/x` stays constant.

**Inverse proportion.** If `y` is inversely proportional to `x` (`y = k/x`),
then doubling `x` halves `y`; the product `x*y` stays constant. (Rate–time
problems at fixed distance are inverse: twice the speed → half the time.)

**Scaling / unit conversion.** Set up a proportion with the unknown isolated.
If 3 machines make 300 parts, then at the same rate 5 machines make
`300 * (5/3) = 500` parts.

---

## 4. Worked mini-examples (answers are ground truth)

1. `20% of 150 = 0.20 * 150 = 30`.
2. `A price of $80 rises 25% → 80 * 1.25 = 100`.
3. `A price of $80 falls 25% → 80 * 0.75 = 60`.
4. `12 is what percent of 48? → 12/48 = 0.25 = 25%`.
5. `Ratio 2:3 of 50 → parts 5, each 10 → 20 and 30`.
6. `5/8 as a percent → 62.5%`.
7. `+10% then +10% → 1.1 * 1.1 = 1.21 → net +21%` (not +20%).

---

## 5. Common traps to encode in good cards

- Successive percent changes multiply; they do not add.
- Percent change divides by the **original** value.
- A percentage-point change is not the same as a percent change.
- A ratio needs a total (or one actual value) before it yields counts.
- Inverse proportion keeps the **product** constant, not the sum.

*End of source. This file is the only content a card may draw its facts from.*
