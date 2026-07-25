"""jettison-parity: the Parity Harness benchmark suite for agent token
optimizers.

Four benchmark families, all deterministic and offline:

- standing_context: standing-context tokens/turn, before vs after
- task_parity:      does optimization change task outcomes?
- session_cost:     cache-aware dollar cost of an N-turn session
- holdout_rct:      holdout RCT framework over recorded request logs
"""

__version__ = "0.1.0"
