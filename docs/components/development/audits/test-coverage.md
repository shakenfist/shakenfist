# Audit: Test coverage

## What we check

### Unit test coverage

We should have solid unit test coverage. No specific coverage
percentage target, but whenever we see something that should be
covered by tests and isn't, we should note it for fixing.

### Functional test coverage

We are obsessed with functional testing. The gold standard is
"do we run the code to do the real thing and does it work as
intended". The goal is to have a test for everything exposed on
the command line or via an API.

For the smaller projects we should be there now and any gap is a
bug to be closed. `shakenfist` is still on a journey towards full
functional coverage.

## Template

No template -- test coverage is project-specific and assessed
holistically.

## Projects

| Project | Unit tests | Functional tests | Issue |
|---------|-----------|-----------------|-------|
| agent-python | needs checking | needs checking | |
| client-python | needs checking | needs checking | |
| clingwrap | needs checking | needs checking | |
| cloudgood | N/A | N/A | - |
| imago | needs checking | compliant | |
| kerbside | needs checking | needs checking | |
| kerbside-patches | N/A | compliant | - |
| library-utilities | needs checking | needs checking | |
| occystrap | needs checking | compliant | |
| ryll | needs checking | needs checking | |
| shakenfist | needs checking | partial | |

N/A for cloudgood: documentation project.
