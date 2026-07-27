# Summary

<!-- What changes, and why. Link the issue if there is one. -->

Closes #

## Type

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] Tests
- [ ] Build, tooling, or CI
- [ ] Refactor (no behaviour change)

## Verification

<!-- The exact commands you ran and their results. "Tests pass" is not enough. -->

```
```

- [ ] Targeted tests pass locally
- [ ] `git diff --check` is clean
- [ ] `python3 tools/scan_secrets.py` passes
- [ ] `python3 tools/check_docs.py` passes (if any docs changed)

For UI changes:

- [ ] `npm run build` and `npm run test:ci` pass
- [ ] I loaded the affected route in a browser and looked at it
- [ ] I reused the existing shared primitives rather than adding a local variant

## Privacy

- [ ] No credential, API key, or token anywhere in this diff
- [ ] No real account identifier, position, cost basis, or transaction
- [ ] No absolute path from my machine
- [ ] No market-data files committed
- [ ] Any screenshots use synthetic or starter data only

## Contracts

Tick anything this PR touches, and confirm an issue was agreed first:

- [ ] An API path, response shape, or field name
- [ ] The price cache format or another on-disk contract
- [ ] Either Python environment's dependencies
- [ ] Research methodology, study parameters, or a published verdict

<!--
Frozen studies are frozen. Do not rerun a spent holdout, retune a parameter to
improve a published result, edit pinned evidence, or soften a verdict.
-->

## Documentation

- [ ] Docs updated in this PR, or no docs are affected

## Notes for the reviewer

<!-- Anything you are unsure about, or deliberately left out of scope. -->
