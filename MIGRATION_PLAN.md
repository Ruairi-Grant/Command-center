# DEV to PROD datastore migration

## Approved scope

- Source: `command-center-dev`
- Target: `command-center-prod`
- Migrate supported records only: Areas, Projects, and Tasks.
- Exclude legacy standalone Goals, Commitments, and DEV activity history.
- Preserve record IDs and parent relationships.
- Normalize legacy task status `inbox` to `active`.
- Reparent supported descendants of excluded legacy goals to their nearest supported ancestor (the Fashion project now sits under Lifestyle).
- Do not overwrite existing PROD records; the migration must abort unless PROD is empty.

## Safety and verification

1. Read and validate the complete DEV source before changing PROD.
2. Confirm the target is empty and the source has no duplicate IDs, orphaned parents, invalid hierarchy, or unsupported records in the selected set.
3. Create a normal atomic backup of the target before writing.
4. Write through the shared `cc_store.py` mutation layer.
5. Verify counts, IDs, parent links, statuses, and computed state after migration.
6. Verify PROD through the running dashboard without changing the selected runtime store.
7. Leave DEV unchanged.

## Result

Migration completed and verified: 19 supported goal-file records (Areas/Projects), 57 Tasks, 6 organisation roots, 1 Waiting item, and 4 Upcoming items are in PROD. Three legacy standalone Goal records were excluded, no Commitments were present, and 30 legacy `inbox` task statuses were normalized to `active`. PROD has one migration activity entry; DEV remains unchanged.

## Rollback

Use the migration backup snapshot if verification fails. No rollback will be attempted automatically after a successful verified migration.
