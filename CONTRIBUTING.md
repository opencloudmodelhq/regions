# Contributing

Thank you for helping.

## Before you start

Open an issue first for anything bigger than a typo, so we can agree on the approach.

## How we work

- **Tests first.** Commit the failing test (red), then the change that makes it pass (green), as two
  commits. Where a test cannot fail first, say so in the pull request.
- **Small functions.** Under 50 lines, cognitive complexity at most 15.
- **Strict typing.** `make check` runs every gate CI runs.
- **Conventional Commits** for every commit message, for example `feat(regions): add rule attribute`.

## Merges

Pull requests are merged by squash, with linear history: one signed commit per pull request reaches `main`.
The failing-test commit and the commit that makes it pass stay visible in the pull request, and their
messages are kept in the body of the squashed commit.

## Sign your work (DCO)

Every commit needs a `Signed-off-by` line certifying the
[Developer Certificate of Origin](https://developercertificate.org/):

```sh
git commit -s
```

Contributions are accepted under the licence of the files they change (see `REUSE.toml`).

## Authorship

You are the author of what you submit and you are responsible for every line.

- Only a human may add `Signed-off-by`.
- Say in the pull request which tools wrote any part of the change.
- Unsolicited bulk or fully generated pull requests will be closed.
- **Data**: every value you add must trace to a primary source you cite. A model's recollection is
  not a source.

## Data and sources

City level only; no street addresses. Public sources only: no NDA, login-gated, paywalled, scraped or
leaked material.

## Security

Never report a vulnerability in an issue. See SECURITY.md.
