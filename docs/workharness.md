# WorkHarness

WorkHarness is a workspace-oriented agent harness that combines task planning,
tool use, file edits, shell execution, and artifact handling into a single
managed agent loop. It is built to support long-horizon, stateful work rather
than isolated single-turn responses.

Compared with the existing PawBench harnesses, WorkHarness is more explicit
about maintaining working state across steps and treating the filesystem,
command line, and generated artifacts as first-class parts of the agent loop.
The benchmark contract stays the same, but the harness implementation is geared
toward broader workspace control and more continuous execution.