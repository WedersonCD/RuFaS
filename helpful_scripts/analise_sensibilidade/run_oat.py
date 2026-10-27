#!/usr/bin/env python3
"""Run GO_campo_dourado feed-price OAT simulations using isolated inputs/outputs."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PIPELINE = Path("GO_campo_dourado/_pipeline_task_manager_metadata.json")
OUTPUT_DIRS = {
    "csv_output_directory": "CSVs",
    "json_output_directory": "JSONs",
    "report_directory": "reports",
    "graphics_directory": "graphics",
    "logs_directory": "logs",
    "save_animals_directory": "animals",
}


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_inputs(root):
    pipeline = read_json(root / PIPELINE)
    tasks = read_json(root / pipeline["files"]["tasks"]["path"])
    if len(tasks["tasks"]) != 1 or tasks["tasks"][0]["task_type"] != "SIMULATION_SINGLE_RUN":
        raise ValueError("Expected exactly one SIMULATION_SINGLE_RUN in the farm pipeline.")
    task = tasks["tasks"][0]
    if task.get("input_root"):
        raise ValueError("This runner expects the farm pipeline's default empty input_root.")
    farm = read_json(root / task["metadata_file_path"])
    feed = read_json(root / farm["files"]["feed"]["path"])
    seen = set()
    for item in feed["feeds"]:
        code, price = item["feed_type"], item["purchased_feed_cost"]
        if type(code) is not int or code in seen:
            raise ValueError(f"Invalid or duplicate feed_type: {code}")
        if type(price) not in (int, float) or not math.isfinite(price) or price < 0:
            raise ValueError(f"Invalid purchased_feed_cost for feed_type {code}: {price}")
        seen.add(code)
    if not seen:
        raise ValueError("No feed ingredients found.")
    return pipeline, tasks, farm, feed


def prepare_scenarios(root, output, inputs, seed, include_baseline=True, feed_types=None):
    """Every scenario starts from the original feed data, never the preceding scenario."""
    pipeline, tasks, farm, feed = inputs
    selected = [f for f in feed["feeds"] if feed_types is None or f["feed_type"] in feed_types]
    if feed_types is not None:
        unknown = set(feed_types) - {f["feed_type"] for f in selected}
        if unknown:
            raise ValueError(f"Unknown feed_type(s): {sorted(unknown)}")
    variants = [(None, "baseline", 1.0)] if include_baseline else []
    variants += [(f, direction, factor) for f in selected for direction, factor in (("minus", 0.8), ("plus", 1.2))]
    scenarios = []
    for ingredient, direction, factor in variants:
        code = ingredient["feed_type"] if ingredient else None
        name = "go_campo_dourado_baseline" if ingredient is None else f"go_campo_dourado_{code}_{direction}_20"
        directory = output / "scenarios" / name
        directory.mkdir(parents=True, exist_ok=False)
        scenario_feed = deepcopy(feed)
        if ingredient is not None:
            for item in scenario_feed["feeds"]:
                if item["feed_type"] == code:
                    item["purchased_feed_cost"] = ingredient["purchased_feed_cost"] * factor
        write_json(directory / "feed.json", scenario_feed)
        scenario_farm = deepcopy(farm)
        scenario_farm["files"]["feed"]["path"] = str(directory / "feed.json")
        write_json(directory / "farm_metadata.json", scenario_farm)
        scenario_tasks = deepcopy(tasks)
        scenario_tasks["parallel_workers"] = 1
        scenario_tasks["input_data_csv_export_path"] = str(directory / "saved_input_data")
        task = scenario_tasks["tasks"][0]
        task.pop("random_seed", None)
        task.update(random_seeds=[seed], output_prefix=name, log_verbosity="logs", suppress_log_files=False)
        task["metadata_file_path"] = str(directory / "farm_metadata.json")
        for key, folder in OUTPUT_DIRS.items():
            task[key] = str(directory / folder)
        # One all-variable CSV per simulation; no dependence on local output filters.
        task["filters_directory"] = str(directory / "filters")
        write_json(directory / "filters" / "csv_oat.json", {"filters": [".*"], "direction": "portrait"})
        write_json(directory / "tasks.json", scenario_tasks)
        scenario_pipeline = deepcopy(pipeline)
        scenario_pipeline["files"]["tasks"]["path"] = str(directory / "tasks.json")
        metadata = directory / "pipeline_metadata.json"
        write_json(metadata, scenario_pipeline)
        scenarios.append({
            "name": name, "feed_type": code, "change_percent": 0 if code is None else (-20 if direction == "minus" else 20),
            "original_price": ingredient["purchased_feed_cost"] if ingredient else None,
            "scenario_price": ingredient["purchased_feed_cost"] * factor if ingredient else None,
            "seed": seed, "metadata": str(metadata), "directory": str(directory),
            "csv": str(output / f"{name}.csv"), "status": "prepared",
        })
    return scenarios


def run_scenario(root, scenario):
    result = dict(scenario)
    directory = Path(scenario["directory"])
    log = directory / "console.log"
    command = [sys.executable, str(root / "main.py"), "-p", scenario["metadata"],
               "-o", str(directory / "output"), "-l", str(directory / "manager_logs"), "-v", "logs"]
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = str(scenario["seed"])
    # Each worker already runs a separate Python process. Avoid nested BLAS parallelism.
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[key] = "1"
    print(f"Starting {scenario['name']}", flush=True)
    try:
        with log.open("w", encoding="utf-8") as stream:
            process = subprocess.run(command, cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False)
        completed, failed = False, False
        with log.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                completed |= "Simulation completed" in line
                failed |= any(marker in line for marker in (
                    "Failed to finish task:", "Task(s) failed", "Early termination",
                    "task not run", "Traceback (most recent call last)",
                ))
        csvs = list((directory / "CSVs").glob("*_saved_variables_csv_oat.json_*.csv"))
        if process.returncode or failed or not completed:
            raise RuntimeError(f"Simulation failed or did not confirm completion (exit={process.returncode}); see {log}")
        if len(csvs) != 1 or csvs[0].stat().st_size == 0:
            raise RuntimeError(f"Expected one nonempty output CSV, found {len(csvs)}; see {log}")
        shutil.copyfile(csvs[0], result["csv"])
        result["status"] = "success"
    except Exception as error:
        result.update(status="failed", error=str(error))
    print(f"{result['status']}: {scenario['name']}", flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3, help="Simultaneous simulations (default: 3).")
    parser.add_argument("--output-dir", type=Path, help="New directory for this experiment; must not already exist.")
    parser.add_argument("--seed", type=int, help="Same nonzero random seed for every scenario (farm seed by default).")
    parser.add_argument("--feed-types", type=int, nargs="+", help="Optionally run only these feed_type codes.")
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Write inputs/manifest without running RuFaS.")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    inputs = load_inputs(ROOT)
    task = inputs[1]["tasks"][0]
    seeds = task.get("random_seeds") or [task.get("random_seed", 44)]
    seed = args.seed if args.seed is not None else seeds[0]
    if type(seed) is not int or not 1 <= seed <= 4294967295:
        parser.error("Choose a fixed --seed between 1 and 4294967295; zero auto-generates a different seed per run.")
    output = args.output_dir or ROOT / "output" / "analise_sensibilidade" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    scenarios = prepare_scenarios(ROOT, output, inputs, seed, not args.skip_baseline, args.feed_types)
    manifest = output / "manifest.json"
    write_json(manifest, scenarios)
    print(f"{len(scenarios)} scenarios; up to {args.workers} simultaneous simulations; seed={seed}\nOutput: {output}")
    if args.dry_run:
        return 0
    # Threads only supervise independent subprocesses; RuFaS singletons are never shared.
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run_scenario, ROOT, scenario): i for i, scenario in enumerate(scenarios)}
        for future in as_completed(futures):
            scenarios[futures[future]] = future.result()
            write_json(manifest, scenarios)
    failures = sum(s["status"] != "success" for s in scenarios)
    print(f"Finished: {len(scenarios) - failures} succeeded, {failures} failed. Details: {manifest}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
