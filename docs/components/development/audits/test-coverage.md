# Audit: Test coverage

## What we check

**Nothing, here.** This criterion is delegated in full to the pre-push
review, and **coverage for it is reported by the
[push-audit](/components/development/audits/push-audit/) audit**, which checks that every
repository carries the current `functional-test-coverage` shared block
in its `PUSH-AUDIT.md`. This criterion has no compliance table of its
own on [compliance.md](/components/development/audits/compliance/), which lists it under the
criteria with no automated check: a table of who carries the block
would be a second copy of the one that audit already publishes -- and
a table of who has *good tests* is not something any script can fill
in.

This page remains the statement of the standard.

## The standard

### Functional test coverage

We are obsessed with functional testing. The gold standard is "do we
run the code to do the real thing and does it work as intended", and
the goal is a test for everything exposed on the command line or via
an API.

For the smaller projects we should be there now, and any gap is a bug
to be closed. `shakenfist` is still on a journey towards full
functional coverage.

The test that matters for a given change is the one that would have
failed before it and passes after. Mocking the system under test
proves nothing: mock the boundary -- the network, the clock, the
hypervisor -- and let the code being tested actually run.

### Unit test coverage

We should have solid unit test coverage. There is no specific
coverage percentage target, but whenever we see something that should
be covered by tests and isn't, we should note it for fixing. Error
paths and argument validation are where this bites, being the code
most often written once and never run again.

## Why this is not measured

A coverage percentage is not the property we want, and the property we
do want -- "is there a test for everything exposed on the command
line" -- needs someone to read the command line and the test suite and
compare them. A script can count tests; it cannot notice the
subcommand nobody wrote one for.

So the standard is enforced where the gap is created, by the reviewer
that runs before a push. That is the same treatment the
`comment-proportion` block gets, and for the same reason: the audit
verifies the wording is present and current, and does not try to score
the thing itself.

## Template

No template. The reviewer wording is
`templates/shared-blocks/functional-test-coverage.md`; the
[push-audit](/components/development/audits/push-audit/) audit is what checks it is deployed and
current.
