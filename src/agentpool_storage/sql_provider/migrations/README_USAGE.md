# Database Migrations for agentpool

This directory contains Alembic migrations for the agentpool database schema.

## Overview

The project uses [Alembic](https://alembic.sqlalchemy.org/) for database schema migrations. Alembic is the database migration tool for SQLAlchemy, which works seamlessly with SQLModel.

## Quick Start

### Check Migration Status
```bash
# Check current database version
python scripts/db_migrate.py status

# Show migration history
python scripts/db_migrate.py history
```

### Apply Migrations
```bash
# Upgrade to latest migration
python scripts/db_migrate.py upgrade

# Upgrade to specific revision
python scripts/db_migrate.py upgrade abc123
```

### Create New Migrations
```bash
# Create a new migration with auto-detection of model changes
python scripts/db_migrate.py create "Add new field to Message" --autogenerate

# Create empty migration for manual changes
python scripts/db_migrate.py create "Custom database change"
```

### Rollback Changes
```bash
# Downgrade to previous migration
python scripts/db_migrate.py downgrade -1

# Downgrade to specific revision
python scripts/db_migrate.py downgrade abc123

# Reset database (WARNING: destructive)
python scripts/db_migrate.py reset --force
```

## Direct Alembic Commands

You can also use Alembic directly:

```bash
# Check current revision
alembic current

# Show migration history
alembic history

# Upgrade database
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "Description of changes"

# Downgrade
alembic downgrade -1
```

## Configuration

### Database URL
The migration system uses the database URL from:
1. `DATABASE_URL` environment variable
2. Default: `sqlite:///./agentpool.db` (configured in `alembic.ini`)

### Environment Setup
The `migrations/env.py` file (in `src/agentpool_storage/sql_provider/`, shipped with the package) is configured to:
- Import all SQLModel models automatically
- Support both sync and async database connections
- Use UTC timezone for timestamps
- Enable type and server default comparison for better change detection

## Field Name Changes

**Important**: We recently renamed token-related fields in the `Message` model:
- `prompt_tokens` → `input_tokens`
- `completion_tokens` → `output_tokens`

The initial migration (`5ffc5f0266a1`) already includes these new field names. If you have existing data with the old field names, you may need to create a data migration.

## Best Practices

### Creating Migrations
1. Always review auto-generated migrations before applying them
2. Use descriptive messages for migration names
3. Test migrations on a copy of production data first
4. Consider backward compatibility when possible

### Applying Migrations
1. Always backup your database before applying migrations
2. Apply migrations during maintenance windows for production systems
3. Monitor application logs after migration deployment

### Model Changes
When modifying SQLModel models:
1. Make the change in `src/agentpool_storage/sql_provider/models.py`
2. Create a migration: `python scripts/db_migrate.py create "Description" --autogenerate`
3. Review the generated migration file
4. Test the migration locally
5. Apply the migration: `python scripts/db_migrate.py upgrade`

## Troubleshooting

### Migration Conflicts
If you encounter migration conflicts:
```bash
# Check current state
python scripts/db_migrate.py status

# View history to understand the conflict
python scripts/db_migrate.py history

# Manually resolve by editing migration files or using specific revisions
```

### Schema Drift
If your database schema doesn't match the models:
```bash
# Generate a migration to fix differences
python scripts/db_migrate.py create "Fix schema drift" --autogenerate
```

### Reset Everything
If you need to start fresh (WARNING: loses all data):
```bash
python scripts/db_migrate.py reset --force
```

## Files in this Directory

- `env.py` - Alembic environment configuration
- `script.py.mako` - Template for new migration files
- `versions/` - Directory containing all migration files
- `README` - Basic Alembic documentation
- `README_USAGE.md` - This file

## Support

For issues with migrations:
1. Check the Alembic documentation: https://alembic.sqlalchemy.org/
2. Review SQLModel documentation: https://sqlmodel.tiangolo.com/
3. Check project issues on GitHub
