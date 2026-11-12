# Displaying the documentation locally

Install mkdocs-material, and then run `mkdocs serve`, like this:

```
$ pip install mkdocs-material
...
$ mkdocs serve
INFO     -  Building documentation...
INFO     -  Cleaning site directory
INFO     -  Documentation built in 0.67 seconds
INFO     -  [11:54:17] Watching paths for changes: 'docs', 'mkdocs.yml'
INFO     -  [11:54:17] Serving on http://127.0.0.1:8000/
```

# Regenerating protobuf and gRPC code

After modifying `.proto` files or Python enum definitions in `shakenfist/schema/`,
regenerate the protobuf code using:

```
$ tox -e genprotos
```

This tox environment ensures the correct versions of `grpcio-tools` and
`mypy-protobuf` are used (matching `pyproject.toml`), and runs the full
generation pipeline including:

1. Generating protobuf enum definitions from Python source files
2. Compiling all `.proto` files to Python code and type stubs
3. Fixing import statements for the shakenfist package structure

Moving the `grpcio` family of pins in `pyproject.toml` is also a reason to
regenerate, because each generated `*_pb2_grpc.py` records the generator
which wrote it in `GRPC_GENERATED_VERSION`. A pin bump which does not
regenerate leaves the committed stubs disagreeing with the generator the
repository installs, and the next person to run `tox -e genprotos` for an
unrelated reason inherits the diff. `shakenfist/tests/test_generated_proto_stubs.py`
fails when that happens, and when the stubs come from more than one
generator version, so the regeneration is demanded in the pull request
which moves the pin. Commit everything `genprotos` writes, not only the
families your change touches.

# Finding commits made by a human

Now that shakenfist-bot is making a lot of automated commits, its sometimes
nice to be able to see only changes made by a human. I use this command line:

```
git log --no-merges --oneline --invert-grep --perl-regexp \
    --author='^((?!shakenfist-bot).*)$'
```