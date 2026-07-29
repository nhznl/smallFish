# Brokerage refactor agent coordination

This file is the shared mailbox between the implementation agent (Opus) and
the Codex architecture/coordination task. The implementation source of truth is
[`BROKERAGE_REFACTOR_PLAN.md`](BROKERAGE_REFACTOR_PLAN.md); this file records
questions and answers without silently changing that design.

## Operating rules

- Opus appends a question using the template below. Do not rewrite or delete an
  older question or response.
- Question IDs are sequential: `Q-001`, `Q-002`, and so on.
- Use `Blocking: yes` only when proceeding could violate a settled decision,
  corrupt or reinterpret data, break compatibility, or materially change the
  product. Opus pauses the phase for a blocking question.
- For `Blocking: no`, Opus may continue only when the plan already supplies a
  safe default. Record that default in `Provisional action`.
- Codex appends exactly one response section for each unanswered question. It
  does not edit implementation code, run commits, or broaden the phase while
  acting as the monitor.
- After a question is resolved, Opus includes the question and response in the
  related phase commit. Do not commit an `OPEN` or `OWNER_INPUT_REQUIRED`
  question as though the phase were complete.
- If the plan or repository code answers the question, Codex cites the relevant
  file and records an `ANSWERED` response.
- If product ownership is required, Codex records `OWNER_INPUT_REQUIRED` and
  asks the owner in the Codex task. Opus remains paused when the question is
  blocking.
- A response in this file is not permission to change a settled product
  decision. Amend `BROKERAGE_REFACTOR_PLAN.md` explicitly when the owner changes
  one.
- Never include credentials, account identifiers, real positions, or other
  personal financial data in a question or response.
- Re-read this file immediately before appending. Keep each write small to
  reduce collision risk while both agents share the checkout.

## Question template

```markdown
## Q-001

- Status: OPEN
- Asked by: Opus
- Date: YYYY-MM-DD
- Phase: 1-8
- Blocking: yes | no
- Plan/code references: paths and optional line numbers
- Provisional action: none, or the safe documented default

Question text.
```

## Response template

```markdown
## Response to Q-001

- Status: ANSWERED | OWNER_INPUT_REQUIRED
- Answered by: Codex
- Date: YYYY-MM-DD
- Authority: plan/code references, or owner decision required

Answer or concise escalation. State any effect on the current phase and its
automated gate.
```

## Mailbox

No questions yet.
