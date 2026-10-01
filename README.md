# gha-harden

Harden GitHub Actions workflows by pinning action references to commit SHAs.

## 🤸 Usage

Run **`gha-harden`** from anywhere inside a Git repository to pin actions to the
commits their current tags or branches resolve to.
```sh
gha-harden
```

For example, this action reference:
```yaml
- uses: actions/checkout@v4
```

Becomes:
```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
```

With no file paths, **`gha-harden`** updates `.yml` and `.yaml` files
recursively under `.github/workflows` and `.github/actions` in place. Existing
SHA pins are left unchanged but will be annotated.

To modify only specific files, pass their paths as arguments
```sh
gha-harden .github/workflows/ci.yml .github/actions/build/action.yml
```

### Upgrading

Use `--upgrade` to upgrade actions to their latest versions and pin the
resulting commits, including actions that are already SHA-pinned.
```sh
gha-harden --upgrade
```

The latest version is the highest full-version tag (`vX.Y.Z` or `X.Y.Z`) meeting
the minimum age, excluding prereleases. Upgrades can cross major versions.

For example, these action references:
```yaml
- uses: actions/checkout@v4
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
```

Become:
```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
```

### Upgrade without pinning

To upgrade unpinned references to version tags instead of commit SHAs, use
`--upgrade --no-pin`. Existing SHA-pinned references still upgrade to commit
SHAs.
```sh
gha-harden --upgrade --no-pin
```

Major-only tags keep their form: `v1` upgrades to the highest published `vX` tag
meeting the minimum age (for example, `v2`). Other unpinned references, including
major/minor tags such as `v1.2`, upgrade to full-version tags.

For example, these action references:
```yaml
- uses: actions/checkout@v4
- uses: actions/checkout@v4.2.2
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
```

Become:
```yaml
- uses: actions/checkout@v7
- uses: actions/checkout@v7.0.1
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
```

### Filtering

Use `--filter ORG[/REPO]` or `-f ORG[/REPO]` to only update matching actions.
Repeat the option to include multiple owners or repositories. Filters are
case-insensitive and match any of the supplied values.
```sh
gha-harden --filter actions
gha-harden --upgrade -f actions/checkout -f astral-sh
```

Use `--exclude ORG[/REPO]` or `-x ORG[/REPO]` to skip matching actions. This
option can also be repeated and uses the same case-insensitive matching.
Excludes take precedence over includes. Without `--filter`, all actions are
eligible except those excluded.
```sh
gha-harden --exclude actions/checkout --exclude astral-sh
gha-harden -f actions -x actions/checkout .github/workflows/ci.yml
```

### Minimum age

By default, upgrades select tags whose commits are at least 7 days old. Use
`--min-age DURATION` to change this.
```sh
gha-harden --upgrade --min-age 24h
```

or to disable
```sh
gha-harden --upgrade --min-age 0
```

## 📦 Installation

**`gha-harden`** requires Python 3.12 or later, and the [GitHub CLI]
(authenticated).

### From source

From a checkout of this repository, install **`gha-harden`** using [uv]:

```sh
uv tool install .
```

Alternatively, run it directly from the checkout:

```sh
uv run gha-harden --help
```

[GitHub CLI]: https://cli.github.com/
[uv]: https://docs.astral.sh/uv/

## License

This project is distributed under the terms of the MIT license.

See [LICENSE](LICENSE) for details.
