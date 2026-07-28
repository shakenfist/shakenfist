# Development

Developer-facing notes for working on Kerbside itself. See
[AGENTS.md](https://github.com/shakenfist/kerbside/blob/develop/AGENTS.md)
for build commands, conventions, and common tasks, and
[testing.md](/components/kerbside/testing/) for the test harnesses and CI lanes.

## Database migrations

Kerbside uses Alembic for database schema migrations. The migration
files are located in the `alembic/versions/` directory.

### Creating a new migration

```bash
cd /path/to/shakenfist/kerbside
alembic revision -m "description_of_your_changes"
```

This will create a new migration file in `alembic/versions/`. Edit the
generated file to add your schema changes in the `upgrade()` and
`downgrade()` functions.

Example:

```python
def upgrade() -> None:
    op.add_column('table_name', sa.Column('column_name', sa.Type()))

def downgrade() -> None:
    op.drop_column('table_name', 'column_name')
```

### Applying migrations

To apply all pending migrations:

```bash
alembic upgrade head
```

To rollback one migration:

```bash
alembic downgrade -1
```

**Note:** Alembic automatically uses the database URL from the
kerbside configuration, so ensure your kerbside config is properly set
up before running migrations.

## Vendored web assets

### Bootstrap CSS

Kerbside uses bootstrap CSS for styling. This was constructed by
downloading Bootstrap 5.3 and jQuery 3.7.0 and then installing to
`kerbside/api/static/js`.

### Axios

Kerbside's web administration API uses Axios for HTTP requests.
Version 1.6.5 is cached at `kerbside/api/static/js`.
