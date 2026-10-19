# Command Centre Test Plan

Status: Initial TDD test plan derived from `UI_FUNCTIONAL_SPEC_FINAL.md`

This plan is the test contract for the next implementation phases. It does not change product behavior. Existing tests are retained unless they verify behavior explicitly replaced by the approved specification.

## Test approach

The repository currently uses Python 3.14.7 and the standard library only. `pytest`, Hypothesis, and Playwright are not installed. Existing tests are executable Python scripts using assertions and isolated temporary stores.

Initial approach:

- Preserve the existing script-based suites for storage durability, backup/recovery, API security, store switching, and current organisation regression coverage.
- Add a small standard-library test runner so the full suite has one documented command.
- Add specification tests with stable requirement IDs in test names and comments.
- Use isolated temporary PROD/DEV stores for every test that writes data.
- Use real subprocess dashboard servers for API and served-HTML tests.
- Use a browser later only if the environment provides one; until then, test frontend behavior through the served page and deterministic DOM/event shims.
- New product behavior must be implemented only after its test has been observed failing.

## Requirement matrix

Status meanings:

- Covered: an existing test verifies the requirement or its current equivalent.
- Partial: some behavior is tested, but important cases or the approved behavior are missing.
- Missing: no reliable test currently verifies the requirement.

Implementation meanings:

- Implemented: current code appears to provide the approved behavior.
- Partial: current code provides a related or older behavior.
- Missing: current code does not provide the approved behavior.

### Data model and hierarchy

| ID | Requirement / observable behavior | Scenarios and edge cases | Level | Coverage | Implementation | Risk |
|---|---|---|---|---|---|---|
| HIER-001 | Areas are root-only and have no status. | Create root area; reject area with parent; reject area status; render area without status. | Unit/API | Missing | Missing | High |
| HIER-002 | Projects may be root or children of areas. | Create root project; create project under area; reject project under task; reject project under project. | Unit/API | Missing | Partial | High |
| HIER-003 | Tasks may be root or children of areas, projects, or tasks. | Create each allowed relationship; reject unsupported parent types. | Unit/API | Partial | Partial | High |
| HIER-004 | Parent references remain valid and parent choices are type-filtered. | Unknown parent; deleted/missing parent; UI selector only offers valid parents. | Unit/API/UI | Partial | Partial | High |
| HIER-005 | Circular hierarchies are rejected. | Item as its own parent; ancestor moved under descendant; multi-level cycle. | Unit/API | Missing | Missing | High |
| HIER-006 | Moving an item preserves its ID/history and recalculates hierarchy-derived values. | Move task between projects; move to root; move under area; reject invalid move. | Unit/API | Missing | Missing | High |
| HIER-007 | An item with children cannot be deleted. | Attempt parent deletion; verify no mutation; delete/move children individually, then delete parent. | Unit/API | Missing | Missing | High |
| HIER-008 | Hierarchy is unordered and supports expansion/collapse. | Stable structural rendering; nested branches; no ordering semantics in labels or controls. | UI | Partial | Partial | Medium |

### Status and lifecycle

| ID | Requirement / observable behavior | Scenarios and edge cases | Level | Coverage | Implementation | Risk |
|---|---|---|---|---|---|---|
| LIFE-001 | Areas have no status. | Create/update/render area without status; reject status mutation. | Unit/API/UI | Missing | Missing | High |
| LIFE-002 | Projects support Active, Done, Cancelled. | Valid values; reject Waiting; create/update/complete/cancel paths. | Unit/API | Missing | Partial | High |
| LIFE-003 | Tasks support Active, Waiting, Done, Cancelled. | Valid values; reject obsolete inbox/someday; default Active. | Unit/API | Partial | Missing | High |
| LIFE-004 | Waiting tasks require Waiting-on. | Waiting without field rejected; blank rejected; non-Waiting field hidden/optional; return to Active. | Unit/API/UI | Missing | Missing | High |
| LIFE-005 | Parent cannot be completed or cancelled with unfinished children. | Open child blocks Done; open child blocks Cancelled; all children resolved permits transition; completed/cancelled child does not block. | Unit/API | Missing | Missing | High |
| LIFE-006 | Completion requires confirmation and offers Undo. | Confirm/cancel completion; Undo restores prior status and clears completion timestamp; no accidental completion. | API/UI | Missing | Missing | High |
| LIFE-007 | Completed/cancelled items leave active views but remain stored. | Complete task/project; verify state omission and raw persistence; no history toggle required initially. | Unit/API/UI | Partial | Partial | High |
| LIFE-008 | Lifecycle changes preserve completion metadata. | Completion timestamp set; repeated completion; undo; restart persistence. | Unit/API | Partial | Partial | High |

### Dates and blockers

| ID | Requirement / observable behavior | Scenarios and edge cases | Level | Coverage | Implementation | Risk |
|---|---|---|---|---|---|---|
| DATE-001 | Projects and tasks support explicit due dates. | Create/edit/clear each; invalid date; project date shown in editor. | Unit/API/UI | Partial | Partial | High |
| DATE-002 | Task without explicit date inherits nearest ancestor project date. | Direct child; nested task; project under area; no project date. | Unit/API | Partial | Implemented | High |
| DATE-003 | Explicit task date overrides inheritance. | Set task date; change project date; task remains explicit. | Unit/API | Partial | Implemented | High |
| DATE-004 | Clearing task date resumes inheritance. | Clear explicit task date; project date appears again. | Unit/API | Missing | Partial | High |
| DATE-005 | Moving a task changes/removes inherited date. | Move between dated projects; move to root/area; verify effective date and source. | Unit/API | Missing | Partial | High |
| DATE-006 | Date categories use the user’s local calendar date. | Before today = Overdue; equal = Today; after = Upcoming; timezone boundary. | Unit/API | Missing | Partial | High |
| DATE-007 | Overdue, Today, Upcoming are mutually exclusive. | A task appears in exactly one date category; undated task appears in none. | Unit/API | Missing | Partial | High |
| DATE-008 | Waiting task may appear in Waiting and Overdue/Today. | Waiting overdue; waiting due today; waiting future; waiting without date. | Unit/API/UI | Missing | Partial | High |
| DATE-009 | Upcoming initially includes all future-dated tasks. | Date eight days out; future task appears; no artificial seven-day cutoff. | Unit/API | Missing | Partial | Medium |
| DATE-010 | Blockers show original creation date. | Creation date persists across edits, refresh, restart; UI displays it. | Unit/API/UI | Missing | Partial | Medium |

### Creation and editing UI

| ID | Requirement / observable behavior | Scenarios and edge cases | Level | Coverage | Implementation | Risk |
|---|---|---|---|---|---|---|
| UI-001 | One unified form creates areas, projects, and tasks. | Type selection; one POST per submission; successful persistence. | UI/API | Partial | Missing | High |
| UI-002 | Type-specific fields are shown. | Area has no status; project has due date; task has due date/status; Waiting shows required Waiting-on. | UI | Missing | Partial | High |
| UI-003 | Parent selector offers valid choices. | Root; allowed parent types; excludes self/descendants and project parents for projects. | UI/API | Partial | Partial | High |
| UI-004 | Creation shortcuts focus the same form and preselect type. | Add area/project/task; no disconnected editor; parent context retained where applicable. | UI | Missing | Partial | Medium |
| UI-005 | Editing loads and saves current item values. | Title/status/date/notes/Waiting-on; clear fields; failed save leaves form usable. | UI/API | Partial | Partial | High |
| UI-006 | Moving items is supported in the editor. | Current parent shown; valid move saved; invalid move rejected; effective date refreshed. | UI/API | Missing | Missing | High |
| UI-007 | Active organisation view is modern, nested, and excludes completed/cancelled. | Nested render; collapse/expand; no heavy-box requirement; hidden completed data remains stored. | UI | Partial | Partial | Medium |
| UI-008 | Completion control confirms and provides Undo. | Task/project completion; unfinished-child error; cancel confirmation; Undo. | UI/API | Missing | Missing | High |
| UI-009 | Delete control enforces child restriction and confirmation. | Parent with children rejected; child-by-child cleanup; destructive confirmation. | UI/API | Missing | Missing | High |
| UI-010 | Date views show effective dates and blockers. | Today/Overdue/Upcoming/Waiting; inherited marker; creation date for blockers. | UI/API | Partial | Partial | High |

### Store isolation and request consistency

| ID | Requirement / observable behavior | Scenarios and edge cases | Level | Coverage | Implementation | Risk |
|---|---|---|---|---|---|---|
| STORE-001 | PROD and DEV are isolated. | Writes in PROD absent from DEV; writes in DEV absent from PROD; restart returns PROD. | Integration/API | Covered | Implemented | Critical |
| STORE-002 | Invalid store names/paths are rejected. | Path traversal, absolute path, unknown name; no mutation. | Integration/API | Covered | Implemented | Critical |
| STORE-003 | Concurrent sessions do not redirect operations across stores. | Start two clients; bind each request to origin store; switch between requests; verify records land in originating store. | Integration/API | Missing | Missing/unsafe global override | Critical |
| STORE-004 | Responses are associated with originating store. | Delayed response after switch; stale response cannot overwrite current store state. | Integration/UI | Missing | Missing | Critical |
| STORE-005 | Store switch state persists only for the process and resets on restart. | Fresh server defaults PROD; page header matches API and writes. | Integration/API | Covered | Implemented | High |

### Persistence, failure, and refresh behavior

| ID | Requirement / observable behavior | Scenarios and edge cases | Level | Coverage | Implementation | Risk |
|---|---|---|---|---|---|---|
| PERSIST-001 | Operations persist across requests and restart. | Create/edit/move/status; stop/restart; reload state. | Integration/API | Partial | Partial | High |
| PERSIST-002 | Failed requests do not partially mutate data. | Validation failure; malformed JSON; server error; record count and activity unchanged. | Integration/API | Partial | Partial | High |
| PERSIST-003 | Double submissions create one item. | Rapid double click; repeated request token if used; exactly one record. | Integration/UI | Missing | Missing | High |
| PERSIST-004 | Background refresh does not overwrite unsaved form input. | Type/title/notes/due entered before refresh; values remain; refreshed list updates separately. | UI | Missing | Missing | High |
| PERSIST-005 | Browser reload preserves unfinished drafts where practical. | Draft survives reload; PROD and DEV drafts are isolated; submitted draft clears. | UI | Missing | Missing | Medium |
| PERSIST-006 | Existing atomic writes, recovery, backups, and data integrity remain intact. | Preserve current concurrency, atomicity, backup retention, restore, and activity tests. | Unit/integration | Covered | Implemented | Critical |

### Accessibility and responsive behavior

| ID | Requirement / observable behavior | Scenarios and edge cases | Level | Coverage | Implementation | Risk |
|---|---|---|---|---|---|---|
| A11Y-001 | Core controls are keyboard reachable and labelled. | Form labels; buttons; parent/type/status controls; focus after shortcut/editor open. | Browser/E2E | Missing | Missing | Medium |
| A11Y-002 | Basic semantic/accessibility structure is present. | Page language; headings; form controls; status/error feedback; no inaccessible control-only icons. | Browser/E2E | Missing | Partial | Medium |
| RESP-001 | Layout works at phone-sized and desktop widths. | No overlap/clipping; add form usable; hierarchy readable at narrow width. | Browser/E2E | Missing | Partial | Medium |

## Existing test inventory and disposition

### Retained unchanged

- `tests/test_cc.py`: atomic writes, concurrent creates, backup creation, restore, activity log, and legacy default/merge behavior. It is valuable durability coverage, but some assertions encode obsolete `inbox` behavior and need revision rather than deletion.
- `tests/test_backups.py`: backup retention, verification-before-prune, restore, failure paths, lock ordering, same-second naming, and data integrity. Retain as a protected regression suite.
- `tests/test_dashboard.py`: real-server API, malformed input, state visibility, security headers, localhost binding, generic errors, and write integrity. Retain, then update obsolete task default assertions.
- `tests/test_dashboard_store_switch.py`: real subprocess PROD/DEV isolation, invalid names, header wiring, and restart reset. Retain and extend for concurrent request binding.
- `tests/test_dashboard_frontend.py`: served-page handler replay for task creation and completion click isolation. Retain as a compatibility test; add unified-form scenarios rather than duplicating all API tests.

### Retained but requiring specification updates

- `tests/test_organisation.py`: hierarchy and due inheritance are useful, but it currently permits obsolete goals/questions, inbox status, and arbitrary parent types. Add requirement IDs and replace obsolete expectations in small TDD cycles.
- `tests/test_organisation_ui.py`: served tree markers are useful. Extend for supported types, status visibility, waiting-on conditional UI, date views, draft protection, and creation shortcuts.
- `tests/test_organisation_edit.py`: real create/update route coverage is useful. Extend for valid parent constraints, moves, waiting validation, project/task status rules, completion restrictions, and deletion restrictions.

### New initial test infrastructure

- `docs/test-plan.md`: this version-controlled specification-to-test matrix.
- `tests/test_support.py`: isolated-home, subprocess-server, HTTP, JSONL, and cleanup helpers shared by new tests only. Existing suites can migrate incrementally.
- `tests/run_all.py`: deterministic standard-library runner for every executable test script plus the unittest-style specification tests. It reports each command and returns non-zero on any failure.
- `tests/test_spec_hierarchy.py`: first red/green batch for HIER-001 through HIER-005 and LIFE-001/LIFE-003/LIFE-004. These tests intentionally expose the gap between the current model and the approved specification; no product code is changed in this phase.

## Test execution commands

Current baseline command, before new infrastructure:

```text
python tests/test_cc.py
python tests/test_backups.py
python tests/test_dashboard.py
python tests/test_dashboard_frontend.py
python tests/test_dashboard_store_switch.py
python tests/test_organisation.py
python tests/test_organisation_ui.py
python tests/test_organisation_edit.py
```

Target command after the initial infrastructure is added:

```text
python tests/run_all.py
```

The first run is expected to report the new specification tests as failing because product functionality is deliberately not being implemented in this phase. Existing regression suites must remain green.
