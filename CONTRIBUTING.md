# Contributing

Thanks for helping improve coding-agent-cli. This guide covers setup, the coding standard, and how changes get merged.

## Ground rules

- Be respectful. All participation follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- Report security problems privately. See [SECURITY.md](SECURITY.md). Do not open a public issue.
- Open an issue before a large change, so the design can be agreed first.

## Setup

You need Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/sitien173/coding-agent-cli.git
cd coding-agent-cli
uv sync
```

## Checks

Every pull request must pass the same checks CI runs:

```bash
uv run ruff format --check
uv run ruff check
uv run pyright
uv run pytest
```

`uv run ruff format` and `uv run ruff check --fix` correct most style problems.

Live tests run the real CLIs and are excluded by default. Run them only with the CLIs installed and logged in:

```bash
uv run pytest -m live -rP
```

## Coding standard

### Style

- Ruff formats and lints the code. The line length is 120. Configuration lives in `pyproject.toml`.
- Pyright checks types. New code is fully annotated, and `pyright` must report 0 errors.
- Write `from __future__ import annotations` at the top of each module.
- Public names are listed in `__all__`. Modules and names that start with `_` are private.

### Design rules

- **No runtime dependencies.** The library uses the standard library only.
- **No implicit permissions.** A driver never adds a permission, approval, or sandbox-bypass flag. Callers pass these through `Request.args`.
- **No leaked processes.** Every exit path kills the CLI's whole process tree: completion, cancel, timeout, and a raising callback.
- **No sensitive logging.** Never log prompts, argv, or environment values.
- **Typed, frozen values.** Requests, results, and events are frozen dataclasses. Unknown values are `None`, never zero.
- **One `RunCompleted`.** Each run emits exactly one, last, after the process tree is gone, with a strictly increasing `sequence`.

### Tests

- Every behavior change comes with a test. Write the failing test first when you can.
- Tests use the fake CLIs in `tests/fixtures/fake_cli`. Unit tests never call a real coding-agent CLI.
- A new driver needs argv tests proving it adds no permission flag for any option combination.

### Adding a driver

1. Subclass `CliDriver` in `src/coding_agent_cli/_drivers/`. Follow `claude.py` or `pi.py`.
2. Declare the driver-owned transport flags and the `portable_flags` it supports.
3. Register it in `_builtin_drivers()` in `registry.py`.
4. Add a driver test module, argv policy tests, and a row in each README table.

## Commits and pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`. Mark breaking changes with `!`, as in `feat!:`.
- Pull requests are squash-merged, so the PR title becomes the commit message. A check enforces the Conventional Commits format on PR titles.
- Keep each pull request to one change. Every changed line should trace to that change.
- Do not edit `CHANGELOG.md` or the version by hand. Both are generated from commit messages at release time.
- Fill in the pull request template.

## Releases

Releases are automated with [release-please](https://github.com/googleapis/release-please):

1. Each push to `main` updates an open release PR. It bumps the version in `pyproject.toml`, `__init__.py`, and `uv.lock`, and adds the changelog entry.
2. While the version is below 1.0, `feat:` bumps the minor version and `fix:` bumps the patch version. A breaking change also bumps the minor version.
3. A maintainer merges the release PR when ready. The workflow tags `vX.Y.Z`, creates the GitHub Release, tests, builds, publishes to PyPI through trusted publishing, and attaches the wheel and sdist to the release.

Publishing waits for approval on the `pypi` environment. Pushing a `vX.Y.Z` tag by hand runs the same build and publish; the tag must match the version in `pyproject.toml`.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
