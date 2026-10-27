"""Exercise the runner with real subprocesses, without importing RuFaS."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_oat as oat


FAKE_MAIN = '''
import json, pathlib, sys, time
read = lambda p: json.loads(pathlib.Path(p).read_text())
metadata = read(sys.argv[sys.argv.index('-p') + 1])
task = read(metadata['files']['tasks']['path'])['tasks'][0]
farm = read(task['metadata_file_path'])
feed = read(farm['files']['feed']['path'])
directory = pathlib.Path(task['metadata_file_path']).parent
start = time.time()
time.sleep(0.15)
csv_dir = pathlib.Path(task['csv_output_directory'])
csv_dir.mkdir(parents=True)
(csv_dir / (task['output_prefix'] + '_saved_variables_csv_oat.json_timestamp.csv')).write_text('price\\n' + str(feed['feeds'][0]['purchased_feed_cost']) + '\\n')
(directory / 'timing.json').write_text(json.dumps([start, time.time()]))
print('Simulation completed')
if pathlib.Path('fail').exists():
    print('Failed to finish task: 1/1')
'''


class OatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "experiment"
        self.inputs = (
            {"files": {"tasks": {"path": "tasks.json"}}},
            {"tasks": [{"task_type": "SIMULATION_SINGLE_RUN", "metadata_file_path": "farm.json", "random_seed": 44}]},
            {"files": {"feed": {"path": "feed.json"}, "animal": {"path": "animal.json"}}},
            {"feeds": [{"feed_type": 51, "purchased_feed_cost": 0.16},
                       {"feed_type": 110, "purchased_feed_cost": 0.18}], "rations": [{"unchanged": True}]},
        )
        for path, data in zip((oat.PIPELINE, "tasks.json", "farm.json", "feed.json"), self.inputs):
            oat.write_json(self.root / path, data)
        (self.root / "main.py").write_text(FAKE_MAIN)

    def prepare(self):
        return oat.prepare_scenarios(self.root, self.output, self.inputs, 44)

    def test_oat_prices_and_isolation(self):
        before = deepcopy(self.inputs)
        scenarios = self.prepare()
        self.assertEqual(len(scenarios), 5)
        for scenario in scenarios:
            directory = Path(scenario["directory"])
            feed = oat.read_json(directory / "feed.json")
            changed = [f["feed_type"] for f, base in zip(feed["feeds"], before[3]["feeds"]) if f != base]
            self.assertEqual(changed, [] if scenario["feed_type"] is None else [scenario["feed_type"]])
            if changed:
                factor = 1 + scenario["change_percent"] / 100
                self.assertAlmostEqual(scenario["scenario_price"], scenario["original_price"] * factor)
            task = oat.read_json(directory / "tasks.json")["tasks"][0]
            self.assertEqual(task["random_seeds"], [44])
            self.assertNotIn("random_seed", task)
            for key in oat.OUTPUT_DIRS:
                self.assertTrue(Path(task[key]).is_relative_to(directory))
            self.assertEqual(feed["rations"], before[3]["rations"])
        self.assertEqual(self.inputs, before)
        self.assertEqual(oat.read_json(self.root / "feed.json"), before[3])

    def test_parallel_processes_and_csv_names(self):
        with patch.object(oat, "ROOT", self.root):
            self.assertEqual(oat.main(["--output-dir", str(self.output), "--workers", "3"]), 0)
        scenarios = oat.read_json(self.output / "manifest.json")
        self.assertEqual(len(list(self.output.glob("*.csv"))), 5)
        self.assertTrue((self.output / "go_campo_dourado_51_minus_20.csv").exists())
        self.assertAlmostEqual(float((self.output / "go_campo_dourado_51_plus_20.csv").read_text().splitlines()[1]), 0.192)
        events = []
        for scenario in scenarios:
            self.assertEqual(scenario["status"], "success")
            start, end = oat.read_json(Path(scenario["directory"]) / "timing.json")
            events.extend([(start, 1), (end, -1)])
        active = peak = 0
        for _, change in sorted(events):
            active += change
            peak = max(peak, active)
        self.assertGreaterEqual(peak, 2)
        self.assertLessEqual(peak, 3)

    def test_zero_exit_with_failure_is_rejected(self):
        (self.root / "fail").touch()
        scenario = self.prepare()[0]
        result = oat.run_scenario(self.root, scenario)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(Path(scenario["csv"]).exists())

    def test_missing_csv_is_rejected(self):
        (self.root / "main.py").write_text("print('Simulation completed')\n")
        result = oat.run_scenario(self.root, self.prepare()[0])
        self.assertEqual(result["status"], "failed")

    def test_dry_run_and_existing_output_protection(self):
        with patch.object(oat, "ROOT", self.root):
            args = ["--dry-run", "--skip-baseline", "--feed-types", "51", "--output-dir", str(self.output)]
            self.assertEqual(oat.main(args), 0)
            self.assertEqual(len(oat.read_json(self.output / "manifest.json")), 2)
            self.assertFalse(list(self.output.rglob("console.log")))
            with self.assertRaises(FileExistsError):
                oat.main(args)

    def test_invalid_feed_and_seed_are_rejected(self):
        with self.assertRaises(ValueError):
            oat.prepare_scenarios(self.root, self.output, self.inputs, 44, feed_types=[999])
        self.inputs[3]["feeds"].append(self.inputs[3]["feeds"][0])
        oat.write_json(self.root / "feed.json", self.inputs[3])
        with self.assertRaises(ValueError):
            oat.load_inputs(self.root)
        self.inputs[3]["feeds"].pop()
        oat.write_json(self.root / "feed.json", self.inputs[3])
        with patch.object(oat, "ROOT", self.root), self.assertRaises(SystemExit):
            oat.main(["--seed", "0"])


if __name__ == "__main__":
    unittest.main()
