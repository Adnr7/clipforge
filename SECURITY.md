# Security

## Supported code

Security fixes target the current default branch. ClipForge is an early-stage
project; older snapshots do not have a separate maintenance commitment.

## Reporting a vulnerability

If private vulnerability reporting is enabled, use the repository's
**Security → Report a vulnerability** option. Otherwise, use a private contact
method published on a maintainer's GitHub profile. Avoid posting exploit details
or credentials in a public issue before maintainers have reviewed the report.

Include:

- affected revision and operating system;
- a clear description and reproducible steps;
- expected impact and any suggested fix;
- a minimal example using synthetic data, without real API keys or private media.

There is no guaranteed response-time SLA. Maintainers will coordinate a fix and
public disclosure when appropriate.

## Application boundary

ClipForge is designed for a local user and binds to localhost. It has no remote
multi-user authentication layer. Saved credentials reside in local dotenv
settings/provider profiles, not an encrypted vault. Cloud processing sends the
feature-specific inputs described in the [README](README.md#privacy-and-processing).
