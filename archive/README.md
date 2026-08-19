# Archive

Historical implementations kept for reference only.

Code in this directory:

- is not a production entrypoint;
- must not be imported by runtime modules;
- is excluded from runtime import verification;
- may be imported by characterization tests only when the test explicitly targets
  historical behaviour.

Current contents:

- `monitortrades_legacy.py` — gradual-sell and percentage-distribution logic removed
  from the live `monitortrades.py` path;
- `distributor_reference.py` — outdated tests for the archived
  `ProcentDistributor`; retained as historical specification but intentionally not
  collected by the active pytest suite;
- `old_trade/` — superseded generations of the original trading scripts.
