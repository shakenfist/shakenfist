# Phase 3: info and process multi-image support

## Context

This is phase 3 of the
[quay.io tag-based bulk image discovery plan](/components/occystrap/plans/PLAN-quay-label-search/).
It builds on the URI parsing and resolver from
[phase 2](/components/occystrap/plans/PLAN-quay-label-search-phase-02-uri-and-input/).

## Goal

Wire the `quay://` URI scheme into the `info` and `process`
commands in `main.py` so that users can discover and download
images from a quay.io organization.

## Design

### Detection pattern

Both `info_cmd` and `process_cmd` currently call
`uri.parse_uri(source)` and pass the result to
`PipelineBuilder.build_input()`. For `quay://`, we intercept
*before* the pipeline builder — the `quay` scheme is resolved
into a list of `(registry, image, tag)` tuples, and then we
loop over them, constructing a standard `registry.Image` input
for each.

This means the pipeline builder does not need to know about
`quay://` at all.

### Helper function

A shared helper extracts the quay resolution logic so both
commands can use it:

```python
def _resolve_quay_images(source, ctx):
    """Resolve a quay:// URI to a list of image references.

    Args:
        source: The source URI string.
        ctx: Click context with global options.

    Returns:
        List of (registry, image, tag) tuples, or None
        if the source is not a quay:// URI.
    """
```

When the source scheme is not `quay`, returns `None` and the
command proceeds as before (single-image path). When it is
`quay`, it:
1. Parses via `parse_quay_uri()`
2. Determines the token — from `?token=` in the URI options,
   or falling back to `ctx.obj['PASSWORD']`
3. Calls `resolve_quay_uri()` to get the matches
4. Returns the list (may be empty)

### info command changes

```python
@click.command('info')
def info_cmd(ctx, source):
    images = _resolve_quay_images(source, ctx)
    if images is not None:
        # Multi-image path
        all_infos = []
        for i, (registry, image, tag) in enumerate(images):
            click.echo('(%d/%d) %s/%s:%s'
                       % (i+1, len(images), registry, image, tag),
                       err=True)
            input_source = input_registry.Image(
                registry, image, tag, ...)
            all_infos.append(_build_info(input_source))

        if output_format == 'json':
            click.echo(json.dumps(all_infos, indent=2))
        else:
            for i, info in enumerate(all_infos):
                if i > 0:
                    click.echo('')
                    click.echo('---')
                    click.echo('')
                _print_info_text(info)
    else:
        # Existing single-image path (unchanged)
        ...
```

Key decisions:
- Progress lines go to stderr (`err=True`) so they don't
  pollute JSON output
- Text output: sections separated by `---`
- JSON output: array of info objects (not one JSON doc per
  image)
- Empty results: print a message and exit 0 (not an error)

### process command changes

```python
@click.command('process')
def process_cmd(ctx, source, destination, filters):
    images = _resolve_quay_images(source, ctx)
    if images is not None:
        # Multi-image path
        for i, (registry, image, tag) in enumerate(images):
            click.echo('(%d/%d) Processing %s/%s:%s'
                       % (i+1, len(images), registry, image, tag),
                       err=True)
            source_uri = 'registry://%s/%s:%s' % (
                registry, image, tag)
            builder = PipelineBuilder(ctx)
            input_source, output = builder.build_pipeline(
                source_uri, destination, list(filters))
            _fetch(input_source, output)
            # Post-processing (write_bundle) as needed
    else:
        # Existing single-image path (unchanged)
        ...
```

Key decisions:
- Each image gets a fresh `PipelineBuilder` and pipeline, so
  there is no cross-image state leakage.
- The destination is reused for each image. For `dir://` with
  `unique_names=true`, this works correctly because the
  existing `DirWriter` prefixes files per image. For `tar://`,
  this would overwrite the file each time — we should either
  warn about this or generate per-image filenames.
- For `registry://` output, each image is pushed with its
  original `namespace/repo` name. The output URI acts as a
  destination registry, but we need to override the image
  name. This requires passing the original image name to
  `build_output()` — which already happens since
  `build_pipeline()` uses `input_source.image`.
- Progress goes to stderr.

### Registry credential forwarding

The `quay://` URI resolution uses a quay.io API token. But the
actual image pulls use Docker Registry V2 auth. These can be the
same credential (robot account token works for both). The
existing `--username`/`--password` options flow through
`PipelineBuilder` to `registry.Image` for the pulls. For the
quay.io API, we use `--password` (or `?token=` in the URI) as
the API token.

This means `--password` serves double duty: quay.io API token
for discovery, and Docker Registry V2 password for pulling. This
works for robot accounts. If someone needs separate credentials,
they can use `?token=` in the quay URI for the API and
`--password` for the registry.

### Output compatibility

| Output scheme | Multi-image behavior |
|---------------|---------------------|
| `dir://` with `unique_names=true` | Works correctly — each image's layers get unique prefixes |
| `dir://` without `unique_names` | Last image overwrites previous. Log a warning suggesting `unique_names=true` |
| `tar://` | Each image overwrites the previous tarball. Error with a message suggesting `dir://` instead |
| `registry://` | Works — each image pushed with its original name |
| `oci://`, `mounts://` | Same overwrite problem as `dir://` without unique_names. Error for now |
| `docker://` | Each image loaded with its original name. Works |

### Error handling

- If `resolve_quay_uri()` returns an empty list: print
  "No images found matching quay://..." to stderr and exit 0.
- If one image in a multi-image process fails: log the error,
  continue with remaining images, exit 1 at the end if any
  failed. This is a bulk operation — one failure shouldn't
  abort everything.

## Changes summary

### `occystrap/main.py`

- Add import for `quay` module and `parse_quay_uri`
- Add `_resolve_quay_images(source, ctx)` helper
- Modify `info_cmd` to handle multi-image quay:// sources
- Modify `process_cmd` to handle multi-image quay:// sources
- Update help text for both commands to mention `quay://`

### Tests

New tests in `occystrap/tests/test_quay.py` (or a new
`test_quay_commands.py` if the file gets too large):

- `test_info_quay_text` — mock `resolve_quay_uri` returning
  2 images, mock `PipelineBuilder.build_input`, verify text
  output contains both images separated by `---`
- `test_info_quay_json` — same but with `-O json`, verify
  output is a JSON array of 2 info objects
- `test_info_quay_no_matches` — mock empty results, verify
  "No images found" message
- `test_process_quay_basic` — mock `resolve_quay_uri` returning
  2 images, verify `_fetch` called twice
- `test_process_quay_tar_error` — verify `tar://` destination
  with quay:// source produces an error

## Commit plan

A single commit containing:
- Changes to `occystrap/main.py`
- New tests
