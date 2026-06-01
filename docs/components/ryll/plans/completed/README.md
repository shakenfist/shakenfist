# Completed phase plans

Phase plans live here after their parent master plan flags them
**Complete**. They are kept (rather than deleted) so a future
reader can reconstruct the design history of a feature without
having to chase git blame — the same plan that drove a
sub-agent's brief is often the cleanest record of why a piece
of code looks the way it does.

Move criteria: only plans whose master-plan row reads
"Complete" (no caveats like "X landed, Y pending"). Plans that
are code-landed but still have operator smoke tests, parked
investigations, or open follow-ups stay in `docs/plans/` until
their work is genuinely done.

Convention added during PR #102 review (item 9 — keeps the
active `docs/plans/` directory focused on work in flight).
