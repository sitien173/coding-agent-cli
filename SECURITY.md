# Security Policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Do not open a public issue. Report privately through GitHub:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability**.
3. Describe the problem, the affected version, and steps to reproduce.

You should receive a response within 7 days. Fixed issues are disclosed in a GitHub security advisory and the changelog.

## Scope

In scope:

- A driver adding a permission, approval, or sandbox-bypass flag that the caller did not pass.
- Prompts, argv, or environment values written to logs.
- A CLI process tree surviving the end of a run.
- Temporary files left behind or created with unsafe permissions.

Out of scope: behavior of the coding-agent CLIs themselves, and what an agent does after a caller grants it permissions through `args`.
