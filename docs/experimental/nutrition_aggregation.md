# Experimental nutritional requirement aggregation

This changes only the `NutritionRequirements` targets supplied to ration formulation.
Individual calculations, pen membership, nutritional standards, feed inputs, the SLSQP
solver, and the existing retry/fallback rules are unchanged. It is an experimental
heuristic, not a validated nutritional adequacy rule or a normality test.

## Configuration

Add this inside `ration` in the animal input JSON referenced by simulation metadata:

```json
"nutrition_aggregation": {
  "method": "AUTO_MEDIAN",
  "relative_difference_threshold": 0.10
}
```

Omitting the object defaults to `MEAN` and `0.10`. Herd initialization resets the
strategy for each simulation, including when a subsequent run omits it. Input Manager
validates the method and non-negative threshold; runtime construction also rejects
non-finite thresholds and unknown methods.

| Mode | Selection for eligible targets |
| --- | --- |
| `MEAN` | Existing arithmetic mean, using the original summation order |
| `MEDIAN` | Median |
| `AUTO_MEDIAN` | Median when relative difference strictly exceeds the threshold; otherwise mean |
| `AUTO_P75` | Linear-interpolated 75th percentile when relative difference strictly exceeds the threshold; otherwise mean |

The decision is `abs(mean - median) / max(abs(mean), 1e-12)`. Targets whose absolute
mean is at most `1e-12` retain the mean in every mode, avoiding near-zero amplification.
A value exactly equal to the threshold retains the mean. Quantiles use NumPy's explicit
`linear` interpolation, with no randomness. Constant distributions and empty input
are supported. No individual requirement object is mutated.

## Eligible targets and restrictions

- Metabolizable protein, calcium, phosphorus, and process-based phosphorus can each
  select a statistic independently. The optimizer still takes the larger of the two
  phosphorus requirements, including the existing phosphorus buffer.
- Energy selects a statistic from individual **total** energy requirements. All five
  mean energy components are multiplied by one common factor:
  `selected_total / mean_total`. The `NutritionRequirements.total_energy_requirement`
  property continues to sum these components; it is never assigned independently.
  Mean mode does not multiply components, preserving original numerical behavior.
  Component diagnostics describe their own distributions, but their selected values
  follow the total-energy decision, rather than individual component quantiles.
- This energy scaling preserves the mean component proportions and structural zeros.
  It can scale a nonzero constant component when other components vary. A component's
  selected value is therefore not necessarily its median or P75. This restriction is
  especially important for interpreting maintenance/growth targets and NASEM energy.
  NASEM feed-energy calculations and pen-average body weight, methane, and urine
  nitrogen remain unchanged. Coupling ensures arithmetic consistency, not physiological
  validation of the resulting target vector.
- Dry matter stays at the mean. Its lower bound uses current requirements while its
  upper bound uses initial DMI, which may be a user-provided target. Changing only one
  would create unintended intake bounds. Existing DMI input options and retry increments
  still apply.
- All essential amino acids retain their individual arithmetic means and the existing
  `EssentialAminoAcidRequirements` structure. No amino-acid constraint model is added.
- The initial protein parameter uses the selected aggregate at the start of a
  formulation event. Consequently the existing upper bound (initial protein times
  `PROTEIN_UPPER_LIMIT_FACTOR`) is anchored to that selected target, rather than to the
  old mean. Retries recalculate individual requirements and reaggregate the current
  lower targets; the initial upper protein parameter stays fixed, just as before.

Alternative requirements can still be infeasible with available feeds, fixed manual
ration proportions, or initial upper limits. Changing statistics is not a feasibility
guarantee. The original milk-reduction/DMI retries, manual-ration fallback, previous-ration
fallback, and error when no previous automated ration exists remain in place. Failure
summaries now receive the aggregated requirements used for formulation. Ordinary
mean-based and individual nutritional evaluation remain unchanged, so diagnostic
targets should not be confused with measured animal adequacy.

## Diagnostics and Qlik export

Every optimizer attempt records one flat dictionary for every requirement, every amino
acid, and total energy through `OutputManager.add_variable`. A CSV-compatible filter is:

```json
{
  "name": "nutrition_aggregation",
  "filters": ["Pen._report_nutrition_aggregation.*"]
}
```

Save it as `csv_nutrition_aggregation.json` in the task's `filters_directory` to use
RuFaS's normal CSV export. A `json_` filename produces equivalent JSON output. CSV
columns retain the pen/nutrient prefix and appropriate units; they can be unpivoted
in Qlik to obtain a table of diagnostic rows.

Each value contains `simulation_day`, `pen_id`, `formulation_event_id`, `attempt`,
`nutrient`, `animal_count`, `mean`, `median`, `p75`, `relative_difference`,
`configured_method`, `selected_method`, `selected_value`, and `selection_reason`.
Join records using run/output prefix plus pen ID, event ID, and attempt. Event IDs
increase within each pen and reset with a new pen instance; attempts reset per event.
The day and attempt also associate records with existing failed-constraint summaries.
Diagnostic records are per formulation attempt, not daily records; use their explicit
day rather than interpreting the CSV row index as a simulation day. Calling the private
attempt helper outside the formulation loop uses the sentinel day `-1`, event/attempt
`0`, and is not a production formulation event.

## Validation

Automated coverage includes exact mean compatibility, median, below/above/equal threshold,
P75, zero/near-zero/constant/empty inputs, all amino acids, coupled energy, input rejection,
repeatability and non-mutation. Integration tests exercise NRC and NASEM handoff,
selected initial protein, retries, failure diagnostics, real CSV serialization, manual
fallback and previous-ration fallback under all experimental strategies.

Commands (Python 3.12):

```sh
python -m pytest -q tests/test_biophysical/test_animal tests/test_input_manager.py tests/test_output_manager.py
python -m flake8 RUFAS/biophysical/animal/ration/nutrition_aggregation.py RUFAS/biophysical/animal/pen.py RUFAS/biophysical/animal/herd_manager.py RUFAS/biophysical/animal/ration/ration_manager.py tests/test_biophysical/test_animal/test_ration/test_nutrition_aggregation.py tests/test_biophysical/test_animal/test_pen/test_pen.py
python -m mypy RUFAS/biophysical/animal/ration/nutrition_aggregation.py tests/test_biophysical/test_animal/test_ration/test_nutrition_aggregation.py
```

The broader animal/input/output suite passed **2,633 tests**. Flake8 passed. Mypy 1.15
passed for the new module and its tests. Checking all six changed Python files reports
14 existing errors in inherited code/tests; checking the original versions at branch
commit `c59d8651cb54e8d1de7f267dcb0510c5fca4cce4` reproduces those same 14 errors.
The newest installed Mypy 2.4 crashed in environment CPU-affinity detection, so the
project-supported minimum Mypy version 1.15 was used for verification.

Short simulation validation uses `example_animals_only_metadata.json`, the existing
freestall animal/feed/pen inputs and NASEM standard, seed 42, start `2013:1`, end
`2013:2`. Only the end date and aggregation configuration differ from the example.
Each mode runs in a separate task with the same feed inputs and pen configuration.
See the accompanying PR description for measured smoke-test results. These short
runs validate execution and reporting, not nutritional or economic improvement.
Existing mean-based adequacy flags were false for all three pens even in the baseline;
a successful simulation or optimizer exit must not be equated with those evaluation
flags becoming valid. The discrepancy is outside this aggregation-only change.
