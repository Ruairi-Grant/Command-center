# GitHub branch protection recommendation

Apply these settings to the eventual default branch (`master` until an explicit
rename to `main`):

- Require a pull request before merging.
- Require the following status checks:
  - `Code quality (Ruff and whitespace)`
  - `Type checking (application boundaries)`
  - `Functional tests (legacy, pytest, coverage)`
  - `Security (history secrets and dependencies)`
  - `Python 3.12 compatibility checks`
- Require branches to be up to date before merging, if the repository's merge
  queue or branch model makes that practical.
- Require conversation resolution before merging.
- Do not require an external reviewer; this is a single-developer repository.
- Disable force pushes.
- Disable branch deletion.
- Include administrators in the protection rules where GitHub's selected plan
  supports that option.
- Do not configure deployment approvals or deployment credentials in this phase.

The workflow uses only `pull_request`, `push` to `master`, and manual
`workflow_dispatch` triggers. It does not use `pull_request_target`, production
secrets, Tailscale connectivity, or deployment jobs.

GitHub-hosted CI has not been executed because this branch has not been pushed.
The listed names are the local workflow job names intended to become required
checks after the first hosted run confirms GitHub's displayed check names.
