"""Experimental aggregation of existing requirements; this is not an adequacy rule."""

from copy import deepcopy
from dataclasses import dataclass, fields
from math import isfinite
from typing import Any

import numpy as np

from RUFAS.biophysical.animal.data_types.nutrition_data_structures import NutritionRequirements


@dataclass(frozen=True)
class NutritionAggregation:
    """Select targets without changing individual requirements or animal grouping.

    Energy uses a statistic of individual totals and scales the mean component
    vector uniformly. DMI and amino acids stay at their arithmetic means.
    """

    method: str = "MEAN"
    relative_difference_threshold: float = 0.10

    ENERGY_FIELDS = ("maintenance_energy", "growth_energy", "pregnancy_energy", "lactation_energy", "activity_energy")
    ELIGIBLE_FIELDS = ("metabolizable_protein", "calcium", "phosphorus", "process_based_phosphorus")
    EPSILON = 1e-12

    def __post_init__(self) -> None:
        if self.method not in {"MEAN", "MEDIAN", "AUTO_MEDIAN", "AUTO_P75"}:
            raise ValueError(f"Unknown nutrition aggregation method: {self.method}")
        if not isfinite(self.relative_difference_threshold) or self.relative_difference_threshold < 0:
            raise ValueError("Nutrition aggregation threshold must be finite and non-negative.")

    def _statistics(self, values: list[float], mean: float) -> dict[str, Any]:
        median = float(np.median(values)) if values else 0.0
        p75 = float(np.percentile(values, 75, method="linear")) if values else 0.0
        difference = abs(mean - median) / max(abs(mean), self.EPSILON)
        selected_method = "MEAN"
        if abs(mean) > self.EPSILON:
            if self.method == "MEDIAN":
                selected_method = "MEDIAN"
            elif self.method.startswith("AUTO_") and difference > self.relative_difference_threshold:
                selected_method = "MEDIAN" if self.method == "AUTO_MEDIAN" else "P75"
        selected = {"MEAN": mean, "MEDIAN": median, "P75": p75}[selected_method]
        return dict(
            mean=mean,
            median=median,
            p75=p75,
            relative_difference=difference,
            selected_method=selected_method,
            selected_value=selected,
        )

    def aggregate(
        self, requirements: list[NutritionRequirements]
    ) -> tuple[NutritionRequirements, dict[str, dict[str, Any]]]:
        """Return independent targets and diagnostics, preserving the original mean arithmetic."""
        mean = sum(requirements, NutritionRequirements.make_empty_nutrition_requirements())
        if requirements:
            mean = mean / len(requirements)
        selected = deepcopy(mean)
        diagnostics = {}
        total = self._statistics([r.total_energy_requirement for r in requirements], mean.total_energy_requirement)
        diagnostics["total_energy_requirement"] = total
        scale = (
            total["selected_value"] / mean.total_energy_requirement
            if abs(mean.total_energy_requirement) > self.EPSILON
            else 1.0
        )
        for item in fields(mean):
            name = item.name
            if name == "essential_amino_acids":
                for acid in fields(mean.essential_amino_acids):
                    value = getattr(mean.essential_amino_acids, acid.name)
                    record = self._statistics(
                        [getattr(r.essential_amino_acids, acid.name) for r in requirements], value
                    )
                    record.update(
                        selected_method="MEAN", selected_value=value, selection_reason="amino_acid_mean_preserved"
                    )
                    diagnostics[f"essential_amino_acids.{acid.name}"] = record
                continue
            value = getattr(mean, name)
            record = self._statistics([getattr(r, name) for r in requirements], value)
            if name in self.ENERGY_FIELDS:
                # Avoid even a multiply by 1 in MEAN mode for exact compatibility.
                target = value if total["selected_method"] == "MEAN" else value * scale
                record.update(
                    selected_method=total["selected_method"],
                    selected_value=target,
                    selection_reason="coupled_total_energy_scaling",
                )
            elif name not in self.ELIGIBLE_FIELDS:
                record.update(selected_method="MEAN", selected_value=value, selection_reason="dmi_mean_preserved")
            else:
                record["selection_reason"] = "configured_statistic"
            setattr(selected, name, record["selected_value"])
            diagnostics[name] = record
        total["selected_value"] = selected.total_energy_requirement
        total["selection_reason"] = "total_energy_controls_components"
        return selected, diagnostics
