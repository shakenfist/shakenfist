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

# Finding commits made by a human

Now that shakenfist-bot is making a lot of automated commits, its sometimes
nice to be able to see only changes made by a human. I use this command line:

```
git log --no-merges --oneline --invert-grep --perl-regexp \
    --author='^((?!shakenfist-bot).*)$'
```