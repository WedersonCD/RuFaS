from copy import deepcopy
from typing import Any
from dataclasses import fields

import pytest

from RUFAS.biophysical.animal.data_types.nutrition_data_structures import NutritionRequirements
from RUFAS.biophysical.animal.ration.nutrition_aggregation import NutritionAggregation


def requirements(values: list[float]) -> list[NutritionRequirements]:
    result = []
    for value in values:
        item = NutritionRequirements.make_empty_nutrition_requirements()
        for field in fields(item):
            if field.name != "essential_amino_acids":
                setattr(item, field.name, value)
        item.essential_amino_acids.lysine = value * 2
        result.append(item)
    return result


@pytest.mark.parametrize(
    "method,threshold,expected",
    [
        ("MEAN", 0.1, 4),
        ("MEDIAN", 0.1, 1),
        ("AUTO_MEDIAN", 0.1, 1),
        ("AUTO_MEDIAN", 0.8, 4),
        ("AUTO_MEDIAN", 0.75, 4),
        ("AUTO_P75", 0.1, 5.5),
        ("AUTO_P75", 0.8, 4),
    ],
)
def test_selection(method: str, threshold: float, expected: float) -> None:
    items = requirements([1.0, 1.0, 10.0])
    before = deepcopy(items)
    selected, records = NutritionAggregation(method, threshold).aggregate(items)
    assert selected.calcium == expected
    assert selected.metabolizable_protein == expected
    assert selected.phosphorus == expected
    assert selected.process_based_phosphorus == expected
    assert selected.dry_matter == 4
    assert selected.essential_amino_acids.lysine == 8
    assert records["calcium"]["relative_difference"] == 0.75
    assert items == before
    assert NutritionAggregation(method, threshold).aggregate(items) == (selected, records)


def test_exact_mean_and_all_amino_acids() -> None:
    items = requirements([0.1, 3.5, 10.7])
    for item in items:
        for acid in fields(item.essential_amino_acids):
            setattr(item.essential_amino_acids, acid.name, item.calcium)
    original = sum(items, NutritionRequirements.make_empty_nutrition_requirements()) / len(items)
    assert NutritionAggregation().aggregate(items)[0] == original
    assert NutritionAggregation("MEDIAN").aggregate(items)[0].essential_amino_acids == original.essential_amino_acids


@pytest.mark.parametrize("method", ["MEAN", "MEDIAN", "AUTO_MEDIAN", "AUTO_P75"])
@pytest.mark.parametrize("values", [[], [0.0, 0.0], [1e-15, 1e-15, 1e-13], [4.0, 4.0, 4.0]])
def test_empty_zero_near_zero_constant(method: str, values: list[float]) -> None:
    selected, records = NutritionAggregation(method).aggregate(requirements(values))
    assert selected.calcium == pytest.approx(sum(values) / len(values) if values else 0)
    assert all(record["relative_difference"] >= 0 for record in records.values())


@pytest.mark.parametrize("method", ["MEAN", "MEDIAN", "AUTO_MEDIAN", "AUTO_P75"])
def test_energy_total_controls_components(method: str) -> None:
    items = requirements([1.0, 1.0, 10.0])
    items[0].growth_energy = 0
    items[1].lactation_energy = 0
    selected, records = NutritionAggregation(method).aggregate(items)
    total = records["total_energy_requirement"]
    assert selected.total_energy_requirement == pytest.approx(total["selected_value"])
    mean = NutritionAggregation().aggregate(items)[0]
    ratios = [getattr(selected, name) / getattr(mean, name) for name in NutritionAggregation.ENERGY_FIELDS]
    assert ratios == pytest.approx([ratios[0]] * len(ratios))
    assert all(
        records[name]["selected_method"] == total["selected_method"] for name in NutritionAggregation.ENERGY_FIELDS
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"method": "UNKNOWN"},
        {"relative_difference_threshold": -1},
        {"relative_difference_threshold": float("nan")},
        {"relative_difference_threshold": float("inf")},
    ],
)
def test_invalid_configuration(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        NutritionAggregation(**kwargs)


def test_p75_protein_initial_anchor_avoids_artificial_infeasibility(mocker: Any) -> None:
    from RUFAS.biophysical.animal.ration.ration_optimizer import RationConfig, RationOptimizer
    from RUFAS.biophysical.animal.nutrients.nutrition_supply_calculator import NutritionSupplyCalculator
    from RUFAS.data_structures.feed_storage_to_animal_connection import NutrientStandard

    items = requirements([1.0, 1.0, 1.0, 10.0, 10.0])
    selected, _ = NutritionAggregation("AUTO_P75").aggregate(items)
    config = RationConfig(NutrientStandard.NRC, selected, [], selected.dry_matter, selected.metabolizable_protein)
    mocker.patch.object(NutritionSupplyCalculator, "calculate_nutrient_intake_discount", return_value=1.0)
    mocker.patch.object(NutritionSupplyCalculator, "calculate_metabolizable_protein_supply", return_value=10.0)
    import numpy as np

    vector = np.array([selected.dry_matter])
    assert RationOptimizer.protein_constraint_lower(vector, config) == 0.0
    assert RationOptimizer.protein_constraint_upper(vector, config) > 0.0
    # The old mean upper anchor would contradict the selected P75 lower target.
    config.initial_protein_requirement = sum(r.metabolizable_protein for r in items) / len(items)
    assert RationOptimizer.protein_constraint_upper(vector, config) < 0.0


def test_dmi_mean_preserves_existing_intake_interval() -> None:
    import numpy as np
    from RUFAS.biophysical.animal.ration.ration_optimizer import RationConfig, RationOptimizer
    from RUFAS.data_structures.feed_storage_to_animal_connection import NutrientStandard

    selected, _ = NutritionAggregation("AUTO_P75").aggregate(requirements([1.0, 1.0, 1.0, 10.0, 10.0]))
    config = RationConfig(NutrientStandard.NRC, selected, [], selected.dry_matter, selected.metabolizable_protein)
    config.dmi_constraint_fraction = 0.1
    vector = np.array([selected.dry_matter])
    assert RationOptimizer.DMI_constraint_lower(vector, config) > 0.0
    assert RationOptimizer.DMI_constraint_upper(vector, config) > 0.0
    # Naively selecting DMI P75 with the original upper anchor would make the interval empty.
    selected.dry_matter = 10.0
    assert RationOptimizer.DMI_constraint_lower(vector, config) < 0.0
    assert selected.dry_matter * 0.9 > config.initial_dry_matter_requirement * 1.1
