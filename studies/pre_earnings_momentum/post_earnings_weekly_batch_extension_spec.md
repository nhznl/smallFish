# Friday-only post-earnings historical extension

**Study family:** `pre-earnings-post-event-weekly-batch-extension-v1`

This independent, owner-authorized 2010–2021 replay uses the identical frozen
Friday-only baseline and Risk-On rules as the separate 2022–2025 replay. Each
variant begins at $50,000 in 2010 and carries only its own state through 2021.
It must not be spliced to the 2022–2025 curve, relabelled as a holdout, or used
to revise that replay's exploratory status. It provides retrospective
robustness evidence only.

The wrapper accepts only origin year 2010, years 2010–2021, its own frozen
configs, and `--confirm-weekly-batch-extension-run`. It reads local historical
data only and writes to the independent `post_earnings_weekly_batch_extension`
artifact root.
