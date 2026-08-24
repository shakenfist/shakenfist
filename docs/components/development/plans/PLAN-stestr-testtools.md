# Plan: stestr / testtools version pin

## Problem

As of February 2026, `stestr` 4.2.0 (the latest release) imports
`_b` from `testtools.compat`:

```python
# stestr/repository/file.py line 26
from testtools.compat import _b
```

The `_b` function was removed from `testtools` in version 2.7.0.
This causes an `ImportError` at runtime when the latest versions of
both packages are installed together.

Additionally, `python-subunit` 1.4.5 added a dependency on
`testtools>=2.7`, which makes it impossible to install
`testtools<2.7.0` alongside `python-subunit>=1.4.5` without a
dependency conflict.

## Applied fix

In `tests/requirements.txt` (in the imago repo, and potentially
other repos using the same test stack), we pin:

```
testtools>=2.5.0,<2.7.0
python-subunit>=1.4.0,<1.4.5
```

This keeps all three packages (`stestr`, `testtools`,
`python-subunit`) compatible with each other.

## Affected repositories

Any Shaken Fist repository that uses the stestr/testtools test
stack should apply the same pins. Known affected:

- `imago`
- `shakenfist`

## Conditions for removing the pin

The pins can be removed when **both** of the following are true:

1. **stestr** releases a version that no longer imports `_b` from
   `testtools.compat`. Track this upstream at:
   https://github.com/mtreinish/stestr -- check whether
   `stestr/repository/file.py` still contains
   `from testtools.compat import _b`.

2. **python-subunit** is compatible with whatever testtools version
   stestr supports. As of 1.4.5 it requires `testtools>=2.7`, so
   once stestr supports `testtools>=2.7` as well, the subunit pin
   can also be dropped.

At that point, revert to unpinned upper bounds:

```
testtools>=2.5.0
stestr>=4.0.0
```

and remove the explicit `python-subunit` pin entirely (let stestr
pull it in as a transitive dependency).

## Push audit

This plan has no phases -- it is a pin and a watch on upstream --
so there is no accumulated multi-phase diff to audit. The
obligation still applies to the change that eventually lands: when
the conditions above are met and the pins come out, the unpinning
commit in each affected repository runs that repository's
`PUSH-AUDIT.md` before it is pushed, against that repository's own
default branch. Record here what each audit found, or that it found
nothing.

Both affected repositories carry a `PUSH-AUDIT.md` today, so there
is nothing to rediscover when the pin finally comes out: shakenfist
is in the consistency-audit matrix and `push-audit` reports it
compliant, and imago has one but sits outside the matrix entirely,
so its copy is not version-checked and may have drifted from the
current shared blocks -- check it before relying on it. If either
loses its runbook before the unpinning lands, the commit says so
rather than skipping the step silently.

## Timeline

- **2026-02-18**: Pin applied to imago.
- **2026-02-19**: Pin applied to shakenfist. Also removed
  redundant `uv pip install -U stestr` from CI workflow
  (it was overriding the pyproject.toml pins), and fixed
  one test using `with self.assertRaises()` context manager
  form (incompatible with testtools <2.7.0).
- Check upstream stestr monthly for a fix release.
