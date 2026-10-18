# spokeplan

**Offline bicycle wheel planner: plain-text hub and rim specs in, exact spoke lengths, stock rounding, buy lists and measured-spoke checks out.**

No accounts, no network, no third-party packages. One Python file, standard library only.

## Why

Ordering spokes means working out a length for each side of the hub, rounding up to stock sizes, and getting the buy list right for every wheel you are building. `spokeplan` keeps each wheel's spec in one readable text file and does the geometry the same way every time, with line-numbered errors when the spec is wrong.

## Features

- **Readable wheel files.** One `[section]` per wheel with `key = value` lines and `#` comments.
- **Units where you need them.** Lengths take `mm` or `in` (`58 mm`, `2.28 in`), and bare numbers mean millimetres.
- **Exact spoke lengths per side.** Each side uses its own flange offset, flange diameter and cross count.
- **Crossed and radial lacing.** The lacing angle is `cross × 720° / spokes`, so radial lacing is `cross = 0`.
- **Stock rounding.** Round `up`, `nearest` or `down` to a step (`stock_step`) or to an explicit list of stock lengths (`stock`).
- **Line-numbered lint.** Unknown keys, duplicates, bad units, bad spoke counts, out-of-range crosses, and missing required keys all report `file:line`.
- **Plausibility warnings.** Odd ERD, flange diameter, offset or cross values raise warnings. `--strict` turns them into failures for CI.
- **Buy list.** `buy` totals spokes across every wheel in the file, groups them by stock length, and rounds up to whole packs with `--pack`.
- **Spoke mass.** Given `spoke_mass_g_per_m`, `calc` estimates the spoke mass per wheel.
- **Measured-spoke check.** `check` compares measured spoke lengths with the stock length you ordered, reports mean error and the worst spoke, and exits `3` when the worst error is outside tolerance.
- **Worked explanation.** `explain` prints the formula for each side with the numbers substituted in.
- **JSON output.** `calc --json` and `check --json` print machine-readable results.
- **Scripting exit codes.** `0` ok, `1` input errors, `3` measured spokes out of tolerance.

## Quick start

Lint the example file (it holds a rear and a front wheel):

```console
$ python3 spokeplan.py lint examples/wheels.txt
examples/wheels.txt: 2 wheel(s), 0 error(s), 0 warning(s)
```

Get exact and stock spoke lengths for every side:

```console
$ python3 spokeplan.py calc examples/wheels.txt
```

Then order spokes in packs of ten:

```console
$ python3 spokeplan.py buy examples/wheels.txt --pack 10
```

## Wheel file format

```text
# comments start with #
[rear 700c]
erd = 597 mm            # effective rim diameter
spokes = 32             # must be a multiple of 4
cross_left = 3          # 0 = radial, up to spokes / 4
cross_right = 3
offset_left = 31.0 mm   # hub centre to flange centre, per side
offset_right = 16.0 mm
flange_dia_left = 58 mm
flange_dia_right = 58 mm
rounding = up           # up | nearest | down (optional, default up)
stock_step = 2 mm       # optional, default 1 mm; or use stock = 280, 285, 290
spoke_mass_g_per_m = 1.8  # optional
measured_left = 292.1, 291.8, ...   # optional, one value per spoke on that side
tolerance = 1 mm                    # optional, default 1 mm, used by check (measured vs stock length)
```

`stock` and `stock_step` cannot both be set. Measured lists need exactly `spokes / 2` values per side.

The example values in `examples/wheels.txt` are illustrative. Use your own hub and rim specs.

## Commands

| Command | What it does |
| --- | --- |
| `lint FILE [--strict]` | Report line-numbered errors and warnings |
| `calc FILE [--json] [--strict]` | Exact and stock lengths, lacing angle, spoke count and mass per side |
| `buy FILE [--pack N] [--strict]` | Spokes to order per stock length, rounded to packs |
| `check FILE [--json] [--strict]` | Measured spokes against the stock length ordered; exit `3` if out of tolerance |
| `explain FILE [--wheel NAME] [--strict]` | Formula per side with the numbers filled in |

Exit codes: `0` success, `1` input errors (or warnings with `--strict`), `3` check out of tolerance, `2` bad command-line usage.

## Formula and assumptions

For one side, with `R = ERD / 2`, `r = flange_dia / 2`, `a = offset`, and `θ = cross × 720° / spokes`:

```
L = sqrt(a² + R² + r² − 2·R·r·cos θ)
```

This is the standard straight-line geometry for a spoke running from a flange hole to a rim hole, with the holes separated by the crossed-lacing angle. Check the result against your spoke maker's chart before you cut or order. Lengths are straight-line, so they do not include elbow or threading allowances.

## Quality gates in CI

```console
$ python3 spokeplan.py lint wheels.txt --strict || exit 1
$ python3 spokeplan.py check built-wheels.txt || exit 3
```

## Running tests

```console
$ python3 -m pytest tests
```

The tests use the standard `unittest` module, so they also run without pytest:

```console
$ python3 -m unittest discover -s tests
```

They cover the spoke geometry, stock rounding modes, parser errors with line numbers, buy-list packs, the check thresholds and the CLI exit codes.

## Project layout

```text
spokeplan.py          the whole tool
examples/wheels.txt   a rear and a front wheel to try things on
tests/test_spokeplan.py
README.md
```

## Limits

- Lengths are straight-line only. Elbow, head and thread allowances are not added.
- Lacing is assumed symmetric per side. Asymmetric crosses produce a warning, not a special model.
- It does not model spoke tension, dish or wheel build stress.
