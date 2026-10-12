# Add configurable experimental nutritional requirement aggregation

Ration formulation currently uses mean requirements for every pen. This change adds
`MEAN`, `MEDIAN`, `AUTO_MEDIAN`, and `AUTO_P75` targets before the existing optimizer,
with event diagnostics to make the selected constraints inspectable. Existing inputs
default to the original mean behavior.

### Context

Experimental comparison of statistical requirement targets in RuFaS. No linked issue.
Target branch: `dinamic_centric_metric_for_nutrition`; proposed base: `dev`.

### What

- Add immutable `NutritionAggregation` configuration and deterministic aggregation of
  existing individual requirements.
- Integrate aggregation after requirement calculation and before each optimizer attempt.
- Initialize the protein upper-bound anchor from the selected initial protein target;
  use aggregated targets in failed-constraint diagnostics.
- Report statistics and selected targets for every nutrient, pen, event, and attempt,
  including amino acids and total energy, using existing JSON/CSV output machinery.
- Add unit/integration tests and detailed configuration, restrictions, and export guidance.

### Why

This enables controlled experiments with statistical targets without changing animal
requirements or pen membership. Automatic selection uses the size of the mean–median
difference rather than a normality test. It does not establish that any alternative
provides better individual nutrition or lower feed cost.

### How

Eligible protein and mineral targets independently use the configured statistic.
Automatic methods switch only when `abs(mean - median) / abs(mean)` strictly exceeds
the threshold (default 0.10). A `1e-12` denominator floor and mean retention near zero
prevent numerical instability. P75 explicitly uses linear interpolation.

Energy uses one statistic of individual totals and a common scale for mean components,
so component proportions and the computed total remain consistent. Component targets
are not their independent medians/P75s. Dry matter and all essential amino acids retain
their means. This avoids introducing unintended DMI lower/upper intervals and preserves
the existing amino-acid structure. Body-weight/methane/urine-nitrogen averages and NASEM
feed-energy calculations are unchanged. SLSQP, retries, pen grouping, and fallback logic
are unchanged.

See `docs/experimental/nutrition_aggregation.md` for constraints, numerical rules, and
limitations, including the fixed initial upper protein bound during retries.

### Test plan

- Animal, Input Manager, and Output Manager test suites: **2,633 passed**.
- Pen and aggregation tests run separately: **194 passed**, including independent
  fallback fixtures and real CSV serialization.
- Flake8 passes for all six changed Python files.
- Mypy 1.15 passes for the new module/tests. All changed Python files reproduce the
  same 14 pre-existing type errors as the original branch files. Mypy 2.4 encountered
  an environment CPU-affinity crash; verification used supported Mypy 1.15.
- Tests cover mean equivalence, median, strict threshold behavior, P75, zero/near-zero
  and constant/empty inputs, deterministic results, non-mutation, energy coupling,
  all amino acids, NRC/NASEM handoff, retry identifiers, and original fallbacks.
- Constraint tests demonstrate why selecting P75 protein requires a corresponding
  initial protein anchor and why independently changing DMI would contradict its
  initial upper bound in a skewed distribution.
- Short example simulation: NASEM, seed 42, dates `2013:1`–`2013:2`, using existing
  animals-only metadata and freestall animal/feed/pen inputs. Only aggregation settings
  and the shortened end date differ from the example.

Smoke results (three non-calf pens, 60 diagnostic records per mode):

| Mode | Runtime errors | Attempts per pen | Pen 1 calcium target (g) |
| --- | ---: | ---: | ---: |
| `MEAN` | 0 | 1 | 12.292052 |
| `MEDIAN` | 0 | 1 | 10.987762 |
| `AUTO_MEDIAN` | 0 | 1 | 10.987762 |
| `AUTO_P75` | 0 | 1 | 13.112758 |

Pen 1 calcium's relative difference was `0.10610842256344276`, above 0.10.
Both automatic modes selected alternatives for two diagnostic records and retained
means for 58. All three relevant mean-based adequacy flags were false in every mode,
including baseline. Experimental runs took noticeably longer than MEAN; this was an
observational smoke test, not a performance benchmark. Cost and individual adequacy
improvements were not measured.

### Input Changes

New optional `animal.ration.nutrition_aggregation` object, validated through the existing
Input Manager properties:

```json
{
  "nutrition_aggregation": {
    "method": "AUTO_MEDIAN",
    "relative_difference_threshold": 0.10
  }
}
```

Allowed methods are `MEAN`, `MEDIAN`, `AUTO_MEDIAN`, and `AUTO_P75`. Defaults are `MEAN`
and `0.10`. No example feed, animal grouping, pen configuration, or standard is changed.

### Output Changes

- `Pen._report_nutrition_aggregation.nutrition_aggregation_pen_<id>_<nutrient>`:
  per-attempt statistics, configured and selected methods, selected value, selection
  reason, simulation day, pen ID, animal count, formulation event ID, and attempt.
  Numeric statistics use the nutrient's existing units. Added in mean mode as well.
- Existing failed-constraint summaries describe the actual aggregated formulation
  targets. Ordinary mean and individual nutritional evaluation reporting is unchanged.

#### Filter

Save as `csv_nutrition_aggregation.json` in the simulation task's filters directory:

```json
{
  "name": "nutrition_aggregation",
  "filters": ["Pen._report_nutrition_aggregation.*"]
}
```

Join records by run/output prefix, pen ID, formulation event ID, and attempt. They are
formulation records, not daily data. Use explicit `simulation_day` values in Qlik.

### Limitations

Energy coupling is an experimental proportional scaling of the mean component vector;
it can alter a nonzero constant component and is not a validated physiological target.
DMI and amino acids intentionally do not use alternative statistics in this iteration.
Changed requirements can still make feed constraints infeasible. The existing mean-based evaluation flags were false for all three pens in the
baseline and all experimental smoke runs, despite one optimizer attempt per
pen and no runtime errors. This pre-existing optimizer/evaluator discrepancy is
reported without changing evaluation logic. No economic or nutritional improvement
is claimed; longer runs and individual adequacy/cost analysis
are separate experiments. Changes are intended for review and must not be merged
without that review.
