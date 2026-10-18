"""Tests for spokeplan. Run with: python3 -m pytest tests  (or python3 -m unittest discover -s tests)"""

import contextlib
import io
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import spokeplan  # noqa: E402
from spokeplan import (  # noqa: E402
    check_side,
    describe,
    main,
    parse_text,
    round_to_stock,
    spoke_length,
)

EXAMPLE = ROOT / "examples" / "wheels.txt"

WHEEL = """[rear]
erd = 597 mm
spokes = 32
cross_left = 3
cross_right = 3
offset_left = 31 mm
offset_right = 16 mm
flange_dia_left = 58 mm
flange_dia_right = 58 mm
stock_step = 2 mm
"""


def run_cli(argv):
    """Run main() with stdout and stderr captured. Returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def write_file(directory, text, name="wheels.txt"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


class GeometryTests(unittest.TestCase):
    def test_radial_length_matches_closed_form(self):
        # Radial lacing: L = sqrt(a^2 + (R - r)^2) with R = ERD/2 and r = flange/2.
        length = spoke_length(erd=597, flange_dia=58, offset=31, spokes=32, cross=0)
        expected = math.sqrt(31 ** 2 + (597 / 2 - 58 / 2) ** 2)
        self.assertAlmostEqual(length, expected, places=9)

    def test_three_cross_uses_lacing_angle(self):
        # theta = 3 * 720 / 32 = 67.5 degrees.
        length = spoke_length(erd=597, flange_dia=58, offset=31, spokes=32, cross=3)
        rim, flange = 597 / 2, 58 / 2
        expected = math.sqrt(31 ** 2 + rim ** 2 + flange ** 2
                             - 2 * rim * flange * math.cos(math.radians(67.5)))
        self.assertAlmostEqual(length, expected, places=9)

    def test_crossing_makes_spoke_longer_than_radial(self):
        radial = spoke_length(597, 58, 31, 32, 0)
        crossed = spoke_length(597, 58, 31, 32, 3)
        self.assertGreater(crossed, radial)

    def test_rim_and_flange_on_same_radius_leave_only_the_offset(self):
        # With R == r, the in-plane distance is 2R sin(theta/2) ~ 0 for radial lacing,
        # so the spoke is just the axial offset.
        self.assertAlmostEqual(spoke_length(erd=58, flange_dia=58, offset=31, spokes=32, cross=0), 31)


class RoundingTests(unittest.TestCase):
    def test_step_up(self):
        self.assertAlmostEqual(round_to_stock(290.31, "up", 2), 292.0)

    def test_step_down(self):
        self.assertAlmostEqual(round_to_stock(290.31, "down", 2), 290.0)

    def test_step_nearest(self):
        self.assertAlmostEqual(round_to_stock(291.2, "nearest", 2), 292.0)
        self.assertAlmostEqual(round_to_stock(290.9, "nearest", 2), 290.0)

    def test_exact_multiple_is_not_bumped_up(self):
        self.assertAlmostEqual(round_to_stock(290.0, "up", 2), 290.0)

    def test_stock_list_up_picks_smallest_fitting(self):
        self.assertEqual(round_to_stock(290.31, "up", stock=[280, 285, 290, 295]), 295)

    def test_stock_list_down_picks_largest_not_longer(self):
        self.assertEqual(round_to_stock(290.31, "down", stock=[280, 285, 290, 295]), 290)

    def test_stock_list_nearest_breaks_ties_upward(self):
        self.assertEqual(round_to_stock(290, "nearest", stock=[285, 295]), 295)

    def test_no_stock_long_enough_returns_none(self):
        self.assertIsNone(round_to_stock(300, "up", stock=[280, 290]))


class ParserTests(unittest.TestCase):
    def test_example_file_parses_cleanly(self):
        with open(EXAMPLE, encoding="utf-8") as handle:
            wheels, issues = parse_text(handle.read(), source=str(EXAMPLE))
        self.assertEqual(issues, [])
        self.assertEqual([w.name for w in wheels], ["rear 700c", "front 700c"])
        self.assertEqual(wheels[0].values["erd"], 597.0)
        self.assertEqual(wheels[0].values["offset_right"], 16.0)

    def test_inch_units_are_converted(self):
        text = WHEEL.replace("erd = 597 mm", "erd = 23.5 in")
        wheels, issues = parse_text(text)
        self.assertEqual(issues, [])
        self.assertAlmostEqual(wheels[0].values["erd"], 23.5 * 25.4)

    def test_unknown_key_reports_its_line(self):
        text = WHEEL + "bogus = 1\n"
        _, issues = parse_text(text, source="w.txt")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 11)
        self.assertEqual(issues[0].source, "w.txt")
        self.assertIn("unknown key 'bogus'", issues[0].message)
        self.assertEqual(issues[0].render(), "w.txt:11: error: unknown key 'bogus'")

    def test_duplicate_key_reports_first_line(self):
        text = WHEEL + "erd = 600 mm\n"
        _, issues = parse_text(text)
        self.assertTrue(any("duplicate key 'erd' (first at line 2)" in i.message for i in issues))

    def test_missing_required_keys_are_listed(self):
        _, issues = parse_text("[bare]\nerd = 597\n")
        messages = " ".join(i.message for i in issues)
        self.assertIn("missing required key(s)", messages)
        self.assertIn("spokes", messages)

    def test_spoke_count_must_be_multiple_of_four(self):
        _, issues = parse_text(WHEEL.replace("spokes = 32", "spokes = 30"))
        self.assertTrue(any(i.level == "error" and "multiple of 4" in i.message for i in issues))

    def test_cross_above_quarter_spoke_count_is_an_error(self):
        _, issues = parse_text(WHEEL.replace("cross_left = 3", "cross_left = 9"))
        self.assertTrue(any(i.level == "error" and "cross_left" in i.message for i in issues))

    def test_cross_four_is_only_a_warning(self):
        _, issues = parse_text(WHEEL.replace("cross_left = 3", "cross_left = 4")
                               .replace("cross_right = 3", "cross_right = 4"))
        self.assertTrue(issues)
        self.assertTrue(all(i.level == "warning" for i in issues))

    def test_malformed_header_skips_its_keys_without_cascade(self):
        text = "[broken\nerd = 597\nspokes = 32\n"
        _, issues = parse_text(text)
        self.assertEqual(len(issues), 1)
        self.assertIn("malformed section header", issues[0].message)

    def test_stock_and_stock_step_together_is_an_error(self):
        text = WHEEL + "stock = 280, 290\n"
        _, issues = parse_text(text)
        self.assertTrue(any("either stock or stock_step" in i.message for i in issues))

    def test_measured_count_must_match_spokes_per_side(self):
        text = WHEEL + "measured_left = 290, 291\n"
        _, issues = parse_text(text)
        self.assertTrue(any("needs 16" in i.message for i in issues))

    def test_bad_rounding_word_is_rejected(self):
        _, issues = parse_text(WHEEL + "rounding = sideways\n")
        self.assertTrue(any("rounding must be one of" in i.message for i in issues))


class DescribeTests(unittest.TestCase):
    def test_describe_gives_stock_length_and_counts(self):
        wheels, issues = parse_text(WHEEL)
        self.assertEqual(issues, [])
        result = describe(wheels[0])
        left = result["sides"]["left"]
        self.assertEqual(left["count"], 16)
        self.assertAlmostEqual(left["theta_deg"], 67.5)
        self.assertGreaterEqual(left["stock_mm"], left["exact_mm"])
        self.assertLess(left["stock_mm"] - left["exact_mm"], 2.0)
        self.assertEqual(round(left["stock_mm"]) % 2, 0)
        self.assertIsNone(result["mass_g"])

    def test_mass_is_totalled_when_density_given(self):
        wheels, _ = parse_text(WHEEL + "spoke_mass_g_per_m = 2.0\n")
        result = describe(wheels[0])
        sides = result["sides"]
        expected = 16 * (sides["left"]["stock_mm"] + sides["right"]["stock_mm"]) / 1000 * 2.0
        self.assertAlmostEqual(result["mass_g"], expected, places=6)


class CheckTests(unittest.TestCase):
    def test_check_side_reports_worst_spoke(self):
        summary = check_side([10.0, 12.5, 9.0], exact=10.0, tolerance=1.0)
        self.assertEqual(summary["worst_spoke"], 2)
        self.assertAlmostEqual(summary["worst_error_mm"], 2.5)
        self.assertAlmostEqual(summary["mean_error_mm"], 0.5)
        self.assertFalse(summary["passed"])

    def test_check_side_passes_inside_tolerance(self):
        summary = check_side([10.4, 9.7], exact=10.0, tolerance=0.5)
        self.assertTrue(summary["passed"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_lint_example_is_clean(self):
        code, out, _ = run_cli(["lint", str(EXAMPLE)])
        self.assertEqual(code, 0)
        self.assertIn("0 error(s)", out)

    def test_lint_bad_file_exits_one_with_line_numbers(self):
        path = write_file(self.tmp.name, WHEEL + "bogus = 1\n")
        code, _, err = run_cli(["lint", path])
        self.assertEqual(code, 1)
        self.assertIn(f"{path}:11: error: unknown key 'bogus'", err)

    def test_strict_turns_warnings_into_failure(self):
        path = write_file(self.tmp.name, WHEEL.replace("cross_left = 3", "cross_left = 4")
                          .replace("cross_right = 3", "cross_right = 4"))
        self.assertEqual(run_cli(["lint", path])[0], 0)
        self.assertEqual(run_cli(["lint", path, "--strict"])[0], 1)

    def test_calc_json_has_both_sides(self):
        path = write_file(self.tmp.name, WHEEL)
        code, out, _ = run_cli(["calc", path, "--json"])
        self.assertEqual(code, 0)
        self.assertIn('"left"', out)
        self.assertIn('"right"', out)

    def test_buy_packs_round_up(self):
        path = write_file(self.tmp.name, WHEEL)
        code, out, _ = run_cli(["buy", path, "--pack", "10"])
        self.assertEqual(code, 0)
        # Each distinct stock length is ordered in whole packs of 10.
        counts = {}
        for side in ("left", "right"):
            row = describe(parse_text(WHEEL)[0][0])["sides"][side]
            counts[row["stock_mm"]] = counts.get(row["stock_mm"], 0) + 16
        expected = sum(math.ceil(n / 10) * 10 for n in counts.values())
        self.assertIn(f"total spokes to order: {expected} (packs of 10)", out)

    def test_check_passes_and_fails_with_exit_codes(self):
        stock = describe(parse_text(WHEEL)[0][0])["sides"]["left"]["stock_mm"]
        near = ", ".join(f"{stock + 0.2:.2f}" for _ in range(16))
        far = ", ".join(f"{stock + 3:.2f}" for _ in range(16))
        good = write_file(self.tmp.name, WHEEL + f"measured_left = {near}\n", "good.txt")
        bad = write_file(self.tmp.name, WHEEL + f"measured_left = {far}\n", "bad.txt")
        self.assertEqual(run_cli(["check", good])[0], 0)
        code, out, _ = run_cli(["check", bad])
        self.assertEqual(code, spokeplan.EXIT_OUT_OF_TOLERANCE)
        self.assertIn("FAIL", out)

    def test_check_json_parses_and_keeps_exit_code(self):
        stock = describe(parse_text(WHEEL)[0][0])["sides"]["left"]["stock_mm"]
        far = ", ".join(f"{stock + 3:.2f}" for _ in range(16))
        bad = write_file(self.tmp.name, WHEEL + f"measured_left = {far}\n")
        code, out, _ = run_cli(["check", bad, "--json"])
        self.assertEqual(code, spokeplan.EXIT_OUT_OF_TOLERANCE)
        report = json.loads(out)
        self.assertEqual(report[0]["side"], "left")
        self.assertFalse(report[0]["passed"])

    def test_check_without_measurements_is_an_input_error(self):
        path = write_file(self.tmp.name, WHEEL)
        self.assertEqual(run_cli(["check", path])[0], 1)

    def test_explain_shows_formula_with_numbers(self):
        code, out, _ = run_cli(["explain", str(EXAMPLE), "--wheel", "rear 700c"])
        self.assertEqual(code, 0)
        self.assertIn("theta = 3 x 720 / 32 = 67.500 deg", out)

    def test_missing_file_exits_one(self):
        code, _, err = run_cli(["calc", os.path.join(self.tmp.name, "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err)


if __name__ == "__main__":
    unittest.main()
