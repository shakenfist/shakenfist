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

Retirement is the other ending, and it deletes. A plan is
retired once its reasoning has been moved into the `docs/`
page that owns the topic, where it will be maintained; the
file is then deleted and its row in
[index.md](/components/ryll/plans/index/) becomes a pinned
`blob/<sha>/` permalink. The two endings differ in whether
the design history has a maintained home yet: a plan lands
here when the plan itself is still the cleanest record of
why the code looks as it does, and is retired when that is
no longer true. The process, including which plans are
eligible, is
[docs/plan-retirement.md](https://github.com/shakenfist/development/blob/main/docs/plan-retirement.md)
in shakenfist/development.

Convention added during PR #102 review (item 9 — keeps the
active `docs/plans/` directory focused on work in flight).
The retirement paragraph was added during the #700 review.
