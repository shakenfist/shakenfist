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

# Finding commits made by a human

Now that shakenfist-bot is making a lot of automated commits, its sometimes
nice to be able to see only changes made by a human. I use this command line:

```
git log --no-merges --oneline --invert-grep --perl-regexp \
    --author='^((?!shakenfist-bot).*)$'
```