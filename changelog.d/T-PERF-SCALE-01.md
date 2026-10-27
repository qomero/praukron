- Add `tests/bench_scale.py` and a scale section in `docs/benchmarks.md`:
  deterministic synthetic chronicles of 10k, 40k and 100k lines show that what
  an agent reads (`INDEX.md`, `context`, `retrieve`, one write) stays flat as
  the chronicle grows.
