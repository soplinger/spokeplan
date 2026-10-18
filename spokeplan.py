#!/usr/bin/env python3
"""spokeplan: offline bicycle wheel spoke-length and lacing planner.

Reads a plain-text wheel file (one [section] per wheel, key = value lines)
and works out straight-line spoke lengths for each side of a two-flange
hub, rounds them to stock lengths, totals a buy list and checks measured
spokes against the target. Standard library only.

Exit codes: 0 ok, 1 input errors (or warnings under --strict),
3 measured spokes outside tolerance (check), 2 usage (argparse).
"""

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field

EXIT_OK = 0
EXIT_INPUT = 1
EXIT_OUT_OF_TOLERANCE = 3

SIDES = ("left", "right")
REQUIRED_KEYS = (
    "erd",
    "spokes",
    "cross_left",
    "cross_right",
    "offset_left",
    "offset_right",
    "flange_dia_left",
    "flange_dia_right",
)
OPTIONAL_KEYS = (
    "rounding",
    "stock_step",
    "stock",
    "spoke_mass_g_per_m",
    "measured_left",
    "measured_right",
    "tolerance",
)
ROUNDING_MODES = ("up", "nearest", "down")
DEFAULTS = {
    "rounding": "up",
    "stock_step": 1.0,
    "stock": None,
    "spoke_mass_g_per_m": None,
    "tolerance": 1.0,
}

MM_PER_INCH = 25.4
EPS = 1e-9
LENGTH_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(mm|in)?$", re.IGNORECASE)
DECIMAL_RE = re.compile(r"^\d+(?:\.\d+)?$")
INT_RE = re.compile(r"^\d+$")


@dataclass(frozen=True)
class Issue:
    level: str  # "error" or "warning"
    source: str
    line: int
    message: str

    def render(self):
        return f"{self.source}:{self.line}: {self.level}: {self.message}"


@dataclass
class Wheel:
    name: str
    line: int
    values: dict = field(default_factory=dict)
    lines: dict = field(default_factory=dict)

    def setting(self, key):
        """Value for `key`, falling back to the documented default."""
        return self.values.get(key, DEFAULTS.get(key))


# ---------------------------------------------------------------- parsing

def parse_length(text):
    """Millimetres from '58', '58 mm' or '2.28 in'."""
    match = LENGTH_RE.match(text.strip())
    if not match:
        raise ValueError(f"expected a length such as 58 mm or 2.28 in, got {text!r}")
    value = float(match.group(1))
    if (match.group(2) or "mm").lower() == "in":
        value *= MM_PER_INCH
    return value


def parse_int(text):
    if not INT_RE.match(text.strip()):
        raise ValueError(f"expected a whole number, got {text!r}")
    return int(text.strip())


def parse_decimal(text):
    if not DECIMAL_RE.match(text.strip()):
        raise ValueError(f"expected a plain number, got {text!r}")
    return float(text.strip())


def parse_rounding(text):
    word = text.strip().lower()
    if word not in ROUNDING_MODES:
        raise ValueError(f"rounding must be one of {', '.join(ROUNDING_MODES)}, got {text!r}")
    return word


def parse_length_list(text):
    items = [part.strip() for part in text.split(",")]
    if not all(items):
        raise ValueError("list has an empty item; separate lengths with commas")
    return [parse_length(item) for item in items]


VALUE_PARSERS = {
    "erd": parse_length,
    "spokes": parse_int,
    "cross_left": parse_int,
    "cross_right": parse_int,
    "offset_left": parse_length,
    "offset_right": parse_length,
    "flange_dia_left": parse_length,
    "flange_dia_right": parse_length,
    "rounding": parse_rounding,
    "stock_step": parse_length,
    "stock": parse_length_list,
    "spoke_mass_g_per_m": parse_decimal,
    "measured_left": parse_length_list,
    "measured_right": parse_length_list,
    "tolerance": parse_length,
}


def _validate(wheel):
    """Semantic checks once a section is fully read. Returns (level, line, message) tuples."""
    found = []

    def add(level, message, key=None):
        found.append((level, wheel.lines.get(key, wheel.line), message))

    missing = [key for key in REQUIRED_KEYS if key not in wheel.values]
    if missing:
        add("error", f"missing required key(s): {', '.join(missing)}")
        return found

    spokes = wheel.values["spokes"]
    if spokes < 8 or spokes % 4:
        add("error", f"spokes must be a multiple of 4 and at least 8, got {spokes}", "spokes")
    for side in SIDES:
        cross = wheel.values[f"cross_{side}"]
        key = f"cross_{side}"
        if spokes >= 8 and not 0 <= cross <= spokes // 4:
            add("error", f"{key} must be between 0 and {spokes // 4} for {spokes} spokes, got {cross}", key)
        elif cross > 3:
            add("warning", f"{key} = {cross} is unusual; most wheels use 0 to 3 crosses", key)
    if wheel.values["cross_left"] != wheel.values["cross_right"]:
        add("warning", "cross_left and cross_right differ; confirm the asymmetric lacing is intended", "cross_left")

    erd = wheel.values["erd"]
    if erd <= 0:
        add("error", "erd must be greater than zero", "erd")
    elif not 250 <= erd <= 800:
        add("warning", f"erd {erd:g} mm is outside the usual 250 to 800 mm range", "erd")

    for side in SIDES:
        flange = wheel.values[f"flange_dia_{side}"]
        key = f"flange_dia_{side}"
        if flange <= 0:
            add("error", f"{key} must be greater than zero", key)
        elif not 30 <= flange <= 90:
            add("warning", f"{key} {flange:g} mm is outside the usual 30 to 90 mm range", key)
        offset = wheel.values[f"offset_{side}"]
        if offset > 80:
            add("warning", f"offset_{side} {offset:g} mm is larger than typical", f"offset_{side}")

    if "stock" in wheel.values and "stock_step" in wheel.lines:
        add("error", "use either stock or stock_step, not both", "stock")
    stock = wheel.values.get("stock")
    if stock is not None:
        if any(length <= 0 for length in stock):
            add("error", "stock lengths must be greater than zero", "stock")
        if len(set(stock)) != len(stock):
            add("error", "stock list contains duplicate lengths", "stock")
    if wheel.values.get("stock_step", 1.0) <= 0:
        add("error", "stock_step must be greater than zero", "stock_step")
    if wheel.setting("tolerance") <= 0:
        add("error", "tolerance must be greater than zero", "tolerance")

    for side in SIDES:
        key = f"measured_{side}"
        if key in wheel.values and len(wheel.values[key]) != spokes // 2:
            add(
                "error",
                f"{key} has {len(wheel.values[key])} values but each side needs {spokes // 2}",
                key,
            )
    return found


def parse_text(text, source="<input>"):
    """Parse a wheel file. Returns (wheels, issues); issues carry file:line."""
    wheels, issues = [], []
    names = {}
    current = None
    skipping = False

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            current = None
            skipping = False
            name = line[1:-1].strip() if line.endswith("]") else ""
            if not name:
                issues.append(Issue("error", source, number, f"malformed section header {line!r}; expected [name]"))
                skipping = True
            elif name.lower() in names:
                first = names[name.lower()]
                issues.append(Issue("error", source, number, f"duplicate wheel [{name}] (first at line {first})"))
                skipping = True
            else:
                names[name.lower()] = number
                current = Wheel(name=name, line=number)
                wheels.append(current)
            continue
        if skipping:
            continue
        if current is None:
            issues.append(Issue("error", source, number, "key outside a [wheel] section"))
            continue
        if "=" not in line:
            issues.append(Issue("error", source, number, f"expected 'key = value', got {line!r}"))
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key not in VALUE_PARSERS:
            issues.append(Issue("error", source, number, f"unknown key {key!r}"))
            continue
        if key in current.lines:
            first = current.lines[key]
            issues.append(Issue("error", source, number, f"duplicate key {key!r} (first at line {first})"))
            continue
        if not value:
            issues.append(Issue("error", source, number, f"{key} has no value"))
            continue
        try:
            current.values[key] = VALUE_PARSERS[key](value)
        except ValueError as exc:
            issues.append(Issue("error", source, number, f"{key}: {exc}"))
            continue
        current.lines[key] = number

    for wheel in wheels:
        for level, number, message in _validate(wheel):
            issues.append(Issue(level, source, number, f"[{wheel.name}] {message}"))
    return wheels, issues


# ---------------------------------------------------------------- geometry

def spoke_length(erd, flange_dia, offset, spokes, cross):
    """Straight-line spoke length in mm for one side of a two-flange wheel.

    The hub flange hole sits at axial distance `offset` from the hub centre
    and radius flange_dia / 2. The rim hole sits in the wheel centre plane at
    radius erd / 2. The two holes are separated by the crossed-lacing angle
    theta = cross * 720 / spokes degrees (0 for radial lacing), so
    L = sqrt(a^2 + R^2 + r^2 - 2 R r cos(theta)).
    """
    rim = erd / 2
    flange = flange_dia / 2
    theta = math.radians(cross * 720 / spokes)
    squared = offset ** 2 + rim ** 2 + flange ** 2 - 2 * rim * flange * math.cos(theta)
    return math.sqrt(max(squared, 0.0))


def round_to_stock(length, mode, step=1.0, stock=None):
    """Stock length chosen for `length`, or None when no stock length fits."""
    if stock:
        if mode == "up":
            fits = [s for s in stock if s + EPS >= length]
            return min(fits) if fits else None
        if mode == "down":
            fits = [s for s in stock if s <= length + EPS]
            return max(fits) if fits else None
        # nearest; on a tie the longer spoke wins
        return min(stock, key=lambda s: (abs(s - length), -s))
    quotient = round(length / step, 9)
    if mode == "up":
        count = math.ceil(quotient)
    elif mode == "down":
        count = math.floor(quotient)
    else:
        count = math.floor(quotient + 0.5)
    return round(count * step, 6)


def describe(wheel):
    """Per-side results for one wheel as plain data (JSON friendly)."""
    values = wheel.values
    spokes = values["spokes"]
    erd = values["erd"]
    density = wheel.setting("spoke_mass_g_per_m")
    stock = wheel.setting("stock")
    sides = {}
    total_mass = 0.0 if density is not None else None
    for side in SIDES:
        cross = values[f"cross_{side}"]
        exact = spoke_length(erd, values[f"flange_dia_{side}"], values[f"offset_{side}"], spokes, cross)
        stock_mm = round_to_stock(exact, wheel.setting("rounding"), wheel.setting("stock_step"), stock)
        mass = None
        if density is not None and stock_mm is not None:
            mass = (spokes // 2) * stock_mm / 1000 * density
            total_mass += mass
        sides[side] = {
            "cross": cross,
            "theta_deg": cross * 720 / spokes,
            "exact_mm": exact,
            "stock_mm": stock_mm,
            "count": spokes // 2,
            "mass_g": mass,
        }
    if total_mass is not None and any(sides[s]["mass_g"] is None for s in SIDES):
        total_mass = None
    return {"name": wheel.name, "spokes": spokes, "erd_mm": erd, "sides": sides, "mass_g": total_mass}


def check_side(measured, exact, tolerance):
    """Compare measured spoke lengths with the exact target for one side."""
    deviations = [m - exact for m in measured]
    worst_index = max(range(len(deviations)), key=lambda i: abs(deviations[i]))
    worst = abs(deviations[worst_index])
    return {
        "count": len(measured),
        "mean_error_mm": sum(deviations) / len(deviations),
        "worst_error_mm": worst,
        "worst_spoke": worst_index + 1,
        "passed": worst <= tolerance + EPS,
    }


# ---------------------------------------------------------------- commands

def cmd_calc(wheels, args):
    results = [describe(wheel) for wheel in wheels]
    if args.json:
        print(json.dumps(results, indent=2))
        return EXIT_OK
    for wheel, result in zip(wheels, results):
        print(f"[{result['name']}] {result['spokes']} spokes, ERD {result['erd_mm']:.2f} mm")
        print(f"  {'side':<6} {'cross':>5} {'theta':>10} {'exact mm':>10} {'stock mm':>9} {'spokes':>7}")
        for side in SIDES:
            row = result["sides"][side]
            print(
                f"  {side:<6} {row['cross']:>5} {row['theta_deg']:>7.2f} deg"
                f" {row['exact_mm']:>10.2f} {row['stock_mm']:>9g} {row['count']:>7}"
            )
        if result["mass_g"] is not None:
            print(f"  total spoke mass: {result['mass_g']:.1f} g")
        print()
    return EXIT_OK


def cmd_buy(wheels, args):
    totals = {}
    for wheel in wheels:
        result = describe(wheel)
        for side in SIDES:
            row = result["sides"][side]
            key = row["stock_mm"]
            totals[key] = totals.get(key, 0) + row["count"]
    print(f"{'length mm':>9} {'spokes':>7} {'packs':>6} {'ordered':>8}")
    grand_total = 0
    for length in sorted(totals):
        needed = totals[length]
        packs = math.ceil(needed / args.pack)
        ordered = packs * args.pack
        grand_total += ordered
        print(f"{length:>9g} {needed:>7} {packs:>6} {ordered:>8}")
    print(f"total spokes to order: {grand_total} (packs of {args.pack})")
    return EXIT_OK


def cmd_check(wheels, args):
    report = []
    for wheel in wheels:
        result = describe(wheel)
        for side in SIDES:
            key = f"measured_{side}"
            if key not in wheel.values:
                continue
            row = result["sides"][side]
            summary = check_side(wheel.values[key], row["stock_mm"], wheel.setting("tolerance"))
            summary.update({"wheel": wheel.name, "side": side, "stock_mm": row["stock_mm"],
                            "exact_mm": row["exact_mm"], "tolerance_mm": wheel.setting("tolerance")})
            report.append(summary)
    if not report:
        print("spokeplan: no measured_left or measured_right values to check", file=sys.stderr)
        return EXIT_INPUT
    verdict_code = EXIT_OK if all(row["passed"] for row in report) else EXIT_OUT_OF_TOLERANCE
    if args.json:
        print(json.dumps(report, indent=2))
        return verdict_code
    for row in report:
        verdict = "PASS" if row["passed"] else "FAIL"
        print(
            f"[{row['wheel']}] {row['side']}: {row['count']} measured vs {row['stock_mm']:g} mm stock,"
            f" mean error {row['mean_error_mm']:+.2f} mm, worst {row['worst_error_mm']:.2f} mm"
            f" (spoke {row['worst_spoke']}), tolerance {row['tolerance_mm']:g} mm: {verdict}"
        )
    return verdict_code


def explain_lines(wheel):
    values = wheel.values
    spokes = values["spokes"]
    erd = values["erd"]
    rim = erd / 2
    lines = [f"[{wheel.name}] {spokes} spokes, {spokes // 2} per side, ERD {erd:g} mm"]
    for side in SIDES:
        cross = values[f"cross_{side}"]
        flange = values[f"flange_dia_{side}"] / 2
        offset = values[f"offset_{side}"]
        theta = cross * 720 / spokes
        cos_theta = math.cos(math.radians(theta))
        squared = offset ** 2 + rim ** 2 + flange ** 2 - 2 * rim * flange * cos_theta
        length = spoke_length(erd, values[f"flange_dia_{side}"], offset, spokes, cross)
        lines += [
            f"  {side} side, cross {cross}",
            f"    rim radius     R = ERD / 2 = {rim:.3f} mm",
            f"    flange radius  r = flange_dia / 2 = {flange:.3f} mm",
            f"    flange offset  a = {offset:.3f} mm",
            f"    lacing angle   theta = {cross} x 720 / {spokes} = {theta:.3f} deg, cos = {cos_theta:.6f}",
            f"    L = sqrt(a^2 + R^2 + r^2 - 2Rr cos theta) = sqrt({squared:.3f}) = {length:.3f} mm",
        ]
    return lines


def cmd_explain(wheels, args):
    selected = [w for w in wheels if args.wheel is None or w.name.lower() == args.wheel.lower()]
    if not selected:
        print(f"spokeplan: no wheel named {args.wheel!r}", file=sys.stderr)
        return EXIT_INPUT
    for wheel in selected:
        print("\n".join(explain_lines(wheel)))
        print()
    return EXIT_OK


# ---------------------------------------------------------------- CLI

def build_parser():
    parser = argparse.ArgumentParser(
        prog="spokeplan",
        description="Offline bicycle spoke-length and lacing planner for plain-text wheel files.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("file", help="wheel file (one [section] per wheel)")
    common.add_argument("--strict", action="store_true", help="treat warnings as errors")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("lint", parents=[common], help="check the file and report line-numbered problems")
    calc = sub.add_parser("calc", parents=[common], help="exact and stock spoke lengths per side")
    calc.add_argument("--json", action="store_true", help="print results as JSON")
    buy = sub.add_parser("buy", parents=[common], help="total spokes to order by length")
    buy.add_argument("--pack", type=int, default=1, help="spokes per pack (default 1)")
    check = sub.add_parser("check", parents=[common], help="compare measured spokes with the target")
    check.add_argument("--json", action="store_true", help="print results as JSON")
    explain = sub.add_parser("explain", parents=[common], help="show the formula with numbers filled in")
    explain.add_argument("--wheel", help="only explain the wheel with this name")
    return parser


def is_blocking(issue, strict):
    return issue.level == "error" or (strict and issue.level == "warning")


COMMANDS = {
    "calc": cmd_calc,
    "buy": cmd_buy,
    "check": cmd_check,
    "explain": cmd_explain,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "buy" and args.pack < 1:
        print("spokeplan: --pack must be at least 1", file=sys.stderr)
        return EXIT_INPUT
    try:
        with open(args.file, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"spokeplan: cannot read {args.file}: {exc}", file=sys.stderr)
        return EXIT_INPUT

    wheels, issues = parse_text(text, source=args.file)
    for issue in issues:
        print(issue.render(), file=sys.stderr)
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    if args.command == "lint":
        print(f"{args.file}: {len(wheels)} wheel(s), {len(errors)} error(s), {len(warnings)} warning(s)")
        return EXIT_INPUT if any(is_blocking(i, args.strict) for i in issues) else EXIT_OK

    if any(is_blocking(i, args.strict) for i in issues):
        print(f"spokeplan: {len(errors)} error(s), {len(warnings)} warning(s); fix them or run lint", file=sys.stderr)
        return EXIT_INPUT
    if not wheels:
        print(f"spokeplan: no [wheel] sections found in {args.file}", file=sys.stderr)
        return EXIT_INPUT
    if args.command in ("calc", "buy", "check"):
        for wheel in wheels:
            for side in SIDES:
                row = describe(wheel)["sides"][side]
                if row["stock_mm"] is None:
                    print(
                        f"spokeplan: [{wheel.name}] no stock length covers {row['exact_mm']:.2f} mm ({side} side)",
                        file=sys.stderr,
                    )
                    return EXIT_INPUT
    return COMMANDS[args.command](wheels, args)


if __name__ == "__main__":
    sys.exit(main())
