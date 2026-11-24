# Migration Guidelines

## Golden Rules

1. **Always check existence before dropping** - indexes, columns, tables
2. **Always check non-existence before creating** - same
3. **Make migrations idempotent** - running twice should be safe

## Standard Pattern

```python
def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Early exit if table doesn't exist
    if "my_table" not in inspector.get_table_names():
        return

    columns = {c["name"] for c in inspector.get_columns("my_table")}
    indexes = {idx["name"] for idx in inspector.get_indexes("my_table")}

    # Check before dropping index
    if "ix_my_table_old_col" in indexes:
        op.drop_index("ix_my_table_old_col", table_name="my_table")

    # Check before renaming/adding column
    if "old_col" in columns and "new_col" not in columns:
        op.alter_column("my_table", "old_col", new_column_name="new_col")

    # Check before creating index
    if "ix_my_table_new_col" not in indexes:
        op.create_index("ix_my_table_new_col", "my_table", ["new_col"])
```

## SQLite Limitations

- No direct `ALTER COLUMN` for type changes - use `batch_alter_table`
- No concurrent index creation
- Column renames work but type changes need table rebuild

## Testing

Before committing, test migration against:
1. Fresh database (no tables)
2. Database at previous migration
3. Database already at target state (idempotency)
