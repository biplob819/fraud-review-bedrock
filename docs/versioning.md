# Versioning

The project uses [semantic versioning](https://semver.org): `MAJOR.MINOR.PATCH`, written `v0.1.0` in a tag and `0.1.0` in `pyproject.toml`.

## What each number means

The public surface is:

- the `fraud` command, its subcommands and flags;
- the `policy.md` clause format;
- the alert and summary file formats (`AlertCase`, `summary.json`, the audit log);
- the `config.yaml` keys.

| Change | Bump |
| --- | --- |
| Removes or changes something above in a way that breaks existing use | MAJOR |
| Adds a command, flag, field or check that old use still works with | MINOR |
| Fixes a bug, a doc or a test without changing the surface | PATCH |

While the major number is 0, the surface may still change. A breaking change then raises MINOR, and the changelog says so.

A change to the published results (a new evaluation run) is not a version bump on its own. It is recorded in `docs/measurements/` and the changelog.

## Releases

1. Update `version` in `pyproject.toml` and add a `v<version>` heading to `changelog.md` that lists the changes. The release notes are taken from that heading. If there is none, the workflow uses the newest section and says so in the notes.
2. Merge to `main`, with CI green.
3. Tag the commit `v<version>` and push the tag.
4. The release workflow (`.github/workflows/release.yml`) builds the wheel and the source archive with `uv build`, runs the tests, and attaches both files to a GitHub release with the changelog section as the notes.

The files it attaches are not a standalone install: they do not carry `config.yaml`, `policy.md`, `prices.yaml` or the demo, so run the project from a clone. The workflow checks that the tag matches the version in `pyproject.toml` and that the tagged commit is on `main`, and stops if not. It needs only permission to write repository contents.

## PyPI

Releases are not published to PyPI. The wheel and source archive are attached to the GitHub release only. Publishing to PyPI would be a separate decision by the owner, with its own workflow.

## Support

Only the latest release gets fixes, including security fixes. See `SECURITY.md`.
