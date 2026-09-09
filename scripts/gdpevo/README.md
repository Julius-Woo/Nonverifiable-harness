# GDPevo assessment helpers

These tools run trusted upstream code for the T2 usability assessment. They do
not isolate an untrusted solver. Run them from the trusted controller workspace;
raw results contain oracle answers and must not be exposed to experiment agents.

- `audit_evaluators.py`: checks all 120 selected tasks against references,
  repeats, and five negative controls; preserves raw logs and normalizes the
  group-010 score format. No third-party Python dependencies are required.
- `smoke_services.py`: starts disposable local environment copies with the train
  judge disabled and enabled; checks health, business data, and judge behavior.
  Requires the Flask dependency in `requirements.txt`. It cleans up services and
  temporary copies, and does not require Docker.
- `test_helpers.py`: targeted regression tests for grader-result handling.
- `assessment_results.json`: compact evidence from the 2026-09-09 assessment.

Both command-line helpers require a new `--output` directory and optionally take
`--source PATH` and `--groups 13 17`. Commands, measured results, source revision,
and limitations are in [the assessment](../../docs/gdpevo_assessment.md).
