# Command Centre UI — Expected Functionality

Status: Final draft — awaiting approval

## 1\. Purpose

The dashboard is a simple replacement for notes-based task tracking. It provides one place to capture work, organise it into a hierarchy, manage deadlines, and see active work without exposing completed history by default.

The hierarchy is structural and unordered.

## 2\. Data model shown in the UI

The UI supports:

* Area — a broad domain of responsibility, such as Finance or UAA.
* Project — a bounded piece of work with an optional due date.
* Task — an actionable piece of work.
* Subtask — a task whose parent is another task or project.

A project may contain tasks and subtasks. A task may contain subtasks. An area may contain projects or tasks.

Hierarchy rules:

* Areas cannot have parents.
* Projects can belong to an area or the root.
* Tasks can belong to an area, project, another task, or the root.
* Subtasks (tasks that are part of a task) cannot be owned by a subtask, only a task, project or area.
* Projects cannot contain projects initially.

This keeps the model flexible without unnecessary complexity.

Questions are represented as tasks, phrased as questions where appropriate.

## 3\. PROD and DEV

The dashboard uses two isolated stores:

* PROD — real operational data.
* DEV — test data.

The active store is always visible in the header. A control switches between stores without restarting the dashboard. Every create, edit, move, completion, and deletion action uses the displayed store.

Each operation is bound to the selected store when it is initiated. Switching stores cannot redirect an operation already in progress. Responses must remain associated with their originating store, and stale responses must not overwrite data from the currently displayed store.

Switching does not copy, merge, or migrate records. Restarting returns to PROD. Switching stores does not require confirmation.

## 4\. Page layout

The page contains:

1. Header and active-store indicator.
2. Unified add-item area.
3. Active organisation view.
4. Date-focused task views.
5. Upcoming tasks and active blockers.

The design should be modern and uncluttered, using indentation, whitespace, typography, subtle separators, badges, and expandable branches rather than repetitive heavy boxes.

The layout must work on desktop and phone-sized screens. Controls must not overlap, and background refreshes must not destroy text being entered or change the current layout of page location or collapsed sections.

Background refreshes must not overwrite unsaved form input. Browser reloads should preserve unfinished form drafts where practical, with drafts isolated by store.

Background refreshes should update the refresh time and date if successful, in the users local time.

The header should indicate the version number of the UI.

## 5\. Unified add-item area

There is one creation interface for areas, projects, tasks, and subtasks.

Fields:

* Type: Area, Project, or Task.
* Title — required.
* Parent — optional, selected from the hierarchy.
* Due date — available for projects and tasks.
* Notes — optional.
* Status — available where relevant.

Status rules:

* Areas have no status.
* Projects support Active, Done, and Cancelled.
* Tasks support Active, Waiting, Done, and Cancelled.

The form changes by type:

* Area: title and notes.
* Project: title, parent, due date, notes, and status.
* Task: title, parent, due date, notes, and status.

A subtask is created through the Task type with a task or project as its parent. There are no per-node Add child buttons.

Dedicated Add area, Add project, and Add task shortcuts may be provided. They all focus the same form, preselect the type, and show the appropriate fields.

Submitting creates exactly one item. New tasks default to Active. Success and failure are shown clearly.

## 6\. Organisation view

The organisation view shows the active hierarchy of areas, projects, tasks, and subtasks.

Each active item provides:

* Title.
* Type.
* Status, where supported.
* Edit control.
* Effective due date, where applicable.
* Waiting/blocker information, where applicable.
* Expand/collapse control when it has children.

Items can be expanded and collapsed through several nesting levels. All active tasks appear in the hierarchy, including tasks without dates. The hierarchy is the canonical structural view; date views are filtered views.

Completed and cancelled items are hidden from the default view but remain in the data store. An initial history toggle is not required.

Each active Task and Project displays a completion control directly in its hierarchy row, without requiring the user to open the editor.

* The control allows the user to mark the item Done.
* Completion follows the lifecycle and confirmation rules defined in Section 7.
* A parent with unfinished children cannot be completed; the UI must explain why.
* After successful completion, the item disappears from the active hierarchy.
* The completion action provides an opportunity to Undo, restoring the item to its previous status.
* Completion must preserve the current expanded/collapsed state and scroll position of the Organisation view.
* Areas do not have a completion control.

## 7\. Editing and moving

Edit opens the unified editor populated with the item’s current values.

The editor supports:

* Changing the title.
* Changing supported fields for the item type.
* Setting, changing, or clearing a project due date.
* Setting, changing, or clearing a task due date.
* Changing status.
* Changing parent.
* Saving or cancelling.

Moving an item must:

* Show the current parent.
* Offer valid parent choices.
* Prevent an item becoming its own parent or a descendant of itself.
* Preserve the item’s identity and history.
* Recalculate inherited due dates.

Completion behavior:

* Tasks and projects can be marked Done directly from the Organisation view, date views (where applicable), or the editor.
* Completing an item does not require confirmation.
* A parent cannot be marked Done or Cancelled while it has unfinished children. The user must resolve unfinished children first.
* Successful completion immediately removes the item from active views.
* Completion stores the item's Done status and completion timestamp.
* A temporary success notification provides an Undo action.
* Undo restores the item's previous status and clears its completion timestamp.
* If completion fails, the item must remain visible and an error must be displayed.
* Completing or undoing an item must not reset the current scroll position, expanded/collapsed branches, or unsaved form inputs.

Deletion behavior:

* An item with children cannot be deleted.
* Children must be moved or deleted individually before the parent can be deleted.
* Deletion requires confirmation.

A full history view is out of scope for the initial UI.

## 8\. Due dates

A project may have an explicit due date. A task may have its own explicit due date.

A task without an explicit due date inherits the nearest ancestor project’s due date for display, sorting, and filtering.

Rules:

* An explicit task date overrides an inherited project date.
* Clearing an explicit task date resumes inheritance.
* Changing a project date updates the effective dates of descendant tasks without explicit dates.
* Inherited dates are calculated at read time and are not written into task records.
* The UI distinguishes explicit and inherited dates.
* A project date affects only its descendants.
* Moving a task changes or removes its inherited date according to its new parent hierarchy.

## 9\. Statuses and active blockers

Statuses:

* Active — work is currently live.
* Waiting — progress is blocked on a person, system, or event.
* Done — completed successfully.
* Cancelled — intentionally abandoned.

New tasks default to Active.

A blocker is a Task with Waiting status. It supports:

* A title describing what is blocked or what response is expected.
* A Waiting-on field identifying the person, system, or event.
* The Waiting-on field is visible when status is Waiting and is required in that state.
* An optional due date for when the response/action is expected.
* Its original creation date, shown so its age is clear.
* A way to return it to Active when the dependency clears.

## 10\. Date and task views

Date views show active tasks using their effective due dates and the user’s local calendar date.

Sections:

* Overdue — effective date before today.
* Today — effective date equal to today.
* Upcoming — effective date after today; initially, this can show all future-dated tasks.
* Active blockers/Waiting.

The Overdue, Today, and Upcoming categories are mutually exclusive. A waiting task can appear in both Active blockers/Waiting and Overdue or Today, as appropriate. Waiting status does not suppress date-based views.

The organisation hierarchy contains all active tasks, including undated tasks. There is no separate No date organisation section.

Each date-view item shows its title, effective due date, inherited-date indication where relevant, status, and completion control. Active blockers also show their original creation date.

Completed and cancelled items do not appear in active date views.

## 11\. Feedback and safety

* Create, edit, move, status, and completion actions provide visible success or error feedback.
* Validation identifies the field requiring attention.
* The active store is visible before and after writes.
* Actions never silently write to a different store.
* Internal filesystem paths and exception details are not exposed in the UI.
* Destructive actions, including deletion, require confirmation.
* Hiding completed items never deletes them.
