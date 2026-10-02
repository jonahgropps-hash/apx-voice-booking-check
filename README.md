# APX booking evidence check

An illustrative offline check of a booking claim, its tool trace and destination records. All bundled records and calls are synthetic. No phone calls were made and no provider account or calendar integration was tested.

The checker reports **PASS**, **FAIL** or **UNKNOWN** against an explicit expectation. UNKNOWN means the evidence cannot support acceptance. A reported tool success alone does not establish that an appointment exists.

## Four examples

| Synthetic example | Expected result | Saved report |
| --- | --- | --- |
| [Matching booking](examples/01-matching-booking.json) | PASS | [JSON](reports/01-matching-booking.json) · [HTML](reports/01-matching-booking.html) |
| [Missing booking](examples/02-missing-booking.json) | FAIL | [JSON](reports/02-missing-booking.json) · [HTML](reports/02-missing-booking.html) |
| [Wrong appointment time](examples/03-wrong-time.json) | FAIL | [JSON](reports/03-wrong-time.json) · [HTML](reports/03-wrong-time.html) |
| [Incomplete destination evidence](examples/04-incomplete-evidence.json) | UNKNOWN | [JSON](reports/04-incomplete-evidence.json) · [HTML](reports/04-incomplete-evidence.html) |

## Run locally

Requires Python 3.11 or later. No external packages, credentials or network access are needed. From this directory:

```text
python reconciler.py examples/01-matching-booking.json
python reconciler.py examples/02-missing-booking.json
python reconciler.py examples/03-wrong-time.json
python reconciler.py examples/04-incomplete-evidence.json
python -m unittest discover -s . -p "test_*.py" -v
```

CLI exit codes are PASS = 0, FAIL = 1 and UNKNOWN = 2. The regression suite has 52 synthetic tests. To save a report:

```text
python reconciler.py examples/01-matching-booking.json --json-out reports/01-matching-booking.json --html-out reports/01-matching-booking.html
```

## Evidence limits

Inputs use an explicit normalized schema; this sample contains no production provider adapter. Real use requires verified provider mappings, complete correctly scoped before/after backend records, and human-reviewed caller intent. Completeness, provenance and review annotations are input assertions, not independently verified by this program. The synthetic examples deliberately supply or omit those assertions.

These results illustrate evidence checks. They do not establish voice quality, live workflow performance, recovered bookings, customer results or production reliability. Reports omit raw traces and destination rows; real input files still require appropriate handling.
