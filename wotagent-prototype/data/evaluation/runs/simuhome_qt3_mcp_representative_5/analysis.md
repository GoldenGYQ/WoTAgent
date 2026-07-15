# WoTAgent 2.0 SimuHome qt3 Evaluation

This is a runtime-connected adapted evaluation over the official, unmodified qt3 feasible episodes.
The score follows SimuHome qt3 semantics: required discovery actions and all target assertions must pass.

## Metrics

- Episodes: 5
- Episode success rate: 100.00%
- Goal success rate: 100.00%
- Matter assertion accuracy: 100.00%
- Agent execution success rate: 100.00%
- Mean latency: 3856.1 ms
- Mean TTFT: 1243.7 ms
- Mean tokens per episode: 3702.2
- Total tokens: 18511
- Estimated cost: 0.018168

## Episodes

| Episode | Score | Assertions | Agent status | Error |
|---|---:|---:|---|---|
| qt3_feasible_seed_2.json | 1 | 2/2 | succeeded |  |
| qt3_feasible_seed_4.json | 1 | 2/2 | succeeded |  |
| qt3_feasible_seed_7.json | 1 | 1/1 | succeeded |  |
| qt3_feasible_seed_10.json | 1 | 3/3 | succeeded |  |
| qt3_feasible_seed_13.json | 1 | 3/3 | succeeded |  |

## Scope

Only qt3 feasible immediate-control episodes are included. Temporal and environment-aware qt1/qt2/qt4 remain future work.
