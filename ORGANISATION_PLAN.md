# Command Centre Organisation Plan

Status: design approved; hierarchical model implemented and imported into DEV; editable dashboard UI implemented

## Design goal

Replace the flat notes outline with a user-friendly, agent-readable hierarchy that preserves the quick capture experience while distinguishing long-lived goals, finite projects, actionable tasks, questions, and external commitments.

## Proposed model

Use one tree of `work_items` plus a separate `commitments` collection.

### Work-item types

- `area`: a stable life/work category; not completed. Examples: Finance, UAA, Personal projects, Lifestyle.
- `goal`: an ongoing direction or desired state without a fixed end date. Examples: Fashion, maintain an investment plan.
- `project`: a finite outcome with a clear completion condition. Examples: flights to Dublin for Christmas, sort out US tax, new work phone, UAA Stripe setup.
- `task`: one actionable piece of work that can normally be completed in one focused session. Examples: order a statement, print W-7, clean shoes.
- `question`: an open decision or information request. It is actionable but should remain visibly distinct from ordinary tasks. Examples: is Notion permitted by the data policy?; can donors be named publicly?
- `milestone`: an important checkpoint inside a project, used only when it helps planning. Avoid creating milestones for every ordinary task.

A work item has one `parent_id`, forming a tree. Children are records, not embedded arrays, so an agent can query, reorder, complete, or add subtasks without rewriting a parent. Areas are roots; goals and projects sit below areas; tasks/questions/milestones sit below a goal or project. A task may itself have children when the work genuinely decomposes further.

### Current DEV implementation

The first migration preserves the existing store files for compatibility: container nodes (`area`, `goal`, `project`) are stored in `goals.jsonl`, and leaf nodes (`task`, `question`, `milestone`) are stored in `tasks.jsonl`. Both use `parent_id`; legacy test records without the new fields remain readable. The store validates parent existence, supports `next` and `someday` task statuses, and stores `done_when`, `next_action`, `waiting_on`, and `target` where applicable. PROD was not modified.

### Required fields

```json
{
  "id": "20261019-ab12cd",
  "type": "project",
  "parent_id": "area-uaa",
  "title": "UAA Stripe setup",
  "status": "next",
  "priority": "normal",
  "description": "Connect Stripe and document the expected behaviour.",
  "done_when": "Stripe is configured, tested, and the expected behaviour is documented.",
  "due": null,
  "target": null,
  "next_action": "Confirm the required Stripe/Trello email flow.",
  "waiting_on": null,
  "tags": ["uaa"],
  "created": "...",
  "updated": "...",
  "completed_at": null
}
```

Field rules:

- `type`, `parent_id`, `title`, `status`, `priority`, `created`, and `updated` are always present.
- `due` is an explicit project deadline or task deadline. A task without its own due date inherits the nearest ancestor project's due date for dashboard views and date filtering; inherited dates are marked as inherited and never written into the task record. `target` is an intended date and may be approximate. Do not invent dates.
- `done_when` is required for projects and optional for goals/tasks.
- `next_action` is required for active projects/goals and recommended for any item with children. It is the single best next physical action, not a vague aspiration.
- `waiting_on` is used only when progress depends on a person/system/event; it should identify what is awaited and, when known, the follow-up date.
- `description` holds context, not a second task list.
- Tags are for cross-cutting retrieval, not hierarchy. The parent tree supplies structure.

### Statuses

- `inbox`: captured but not classified or planned.
- `active`: currently being worked on.
- `waiting`: blocked by an external person/event or an unanswered question.
- `someday`: intentionally deferred, with no active commitment.
- `done`: completed and retained for history.
- `cancelled`: intentionally abandoned.

There is no ordered `next` status; ordering is deliberately not implied by the tree.

Only `task`, `question`, and `milestone` should normally appear in the actionable queue. Goals and projects appear there only through their `next_action` when they have no ready child.

### Commitments

Keep commitments separate from work items because they represent promises to another person or organisation. A commitment can link to a work item with `work_item_id`, but it must retain its own `whom`, `what`, `due`, and `status`. This prevents a promise such as "confirm with Max" from being confused with an internal project task.

## How the supplied notes map

- `Finance`, `Personal projects`, and `UAA` become areas.
- `Fashion` becomes an ongoing goal under a Lifestyle area. `Get into it plan` becomes a project or goal plan; `Clean shoes` is a task.
- `Sort out new work phone` becomes a project with `Order new one` and `List everything I need to transfer` as children.
- `Sort out US tax` becomes a project with three child tasks.
- `Sort out missing license`, `Sort out residency`, `Sort out danske bank`, `BOI`, and `ICC sailing paperwork` become projects or tasks based on whether there is a known completion condition and more than one action. Waiting items such as `Waiting on CPR appointment` get `status: waiting` and `waiting_on` metadata.
- `LinkedIn` becomes a project under Personal projects with three child tasks.
- `UAA` becomes an area. `Stripe setup`, `Applications`, `Name change`, `Messaging change`, `Marketing`, `Honorary alumni`, and `Data policy` become projects. Their bullets become child tasks or questions; `Remaining open questions` is a question-collection project only if it needs its own review cadence.
- `Flights to Dublin for Christmas`, `Flights to Edinburgh`, `Plan for bike in Dublin`, and `Buy one of the BCG case books` are standalone tasks initially. They can later be moved under a travel or personal-development project without changing their IDs.

No dates, owners, priorities, or statuses should be inferred from the note. Where the note gives no due date, retain `due: null` and use `inbox` until the user deliberately classifies it.

## Agent decision rules

1. If the title describes an enduring direction and has no finish condition, create a `goal`.
2. If it describes an outcome requiring several actions or a clear end state, create a `project` and write `done_when`.
3. If it is one concrete action, create a `task`.
4. If it asks for information or a decision, create a `question`.
5. If it is a promise to another party, create a linked `commitment` as well as any internal task needed to fulfil it.
6. If a parent has children, select its `next_action` from the first unblocked child rather than inventing a duplicate task.
7. If all children are done, suggest closing the parent; do not close it automatically when the parent has an explicit unresolved question or external dependency.
8. If a task is blocked, set `waiting` and record `waiting_on`; do not leave it in the ready queue.
9. When a user gives broad prompting, propose the smallest useful child task and show its parent path before creating it.
10. Never fabricate dates, dependencies, owners, or completion criteria; ask only when the missing choice changes the plan materially.

## User interface plan

- Default view: collapsible tree grouped by area, with type/status badges and due indicators.
- Each project/goal shows one prominent `Next action` line and a child count.
- Add-child action is available on every goal/project/task with an explicit confirmation of the parent path.
- Inbox view shows uncategorised captures separately from planned work.
- Ready view shows unblocked `inbox` and `active` tasks ordered by due date, priority, age, and parent context.
- Waiting view shows the dependency and follow-up date.
- A compact capture form should accept a title first, then offer parent/type/status as optional fields so capture remains as quick as the notes app.
- Search results should show the full breadcrumb, type, status, next action, and due date.

## Phased implementation plan

1. Add the data-model fields and parent/child validation without importing notes.
2. Add migration-safe store operations: create child, move item, classify item, set next action, and list descendants.
3. Add a read-only tree/ready/waiting API and test it against isolated fixtures.
4. Add dashboard tree rendering and child creation while preserving the current task view.
5. Import the supplied notes into a staging/dev store, review the resulting tree, and only then decide whether any records belong in PROD.
6. Add agent-facing selection rules and regression tests for parent completion, waiting items, orphan prevention, and no fabricated dates.

The next implementation gate is explicit: first agree the hierarchy and whether the proposed work-item types are sufficient; do not migrate the notes into the live store before review.
