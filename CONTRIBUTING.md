# Contributing Guide

Thank you for your interest in contributing to QueryCost Sentinel.

## Before Reporting

- Check existing [Issues](../issues) and [Pull Requests](../pulls) for duplicates.
- Report security vulnerabilities privately following [SECURITY.md](./SECURITY.md).

## Development Setup

The repository recommends `mise` + `uv`, but any Python 3.12 virtual environment works.

```bash
# Example for realtime_monitor
cd script/realtime_monitor
mise exec -- uv venv .venv
mise exec -- uv pip install -r requirements-test.txt

# Or using pip directly
cd script/realtime_monitor
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
```

Repeat the same steps for `script/monthly_ranking` when modifying that stack, or use a single top-level virtual environment that covers both stacks:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r script/realtime_monitor/requirements-test.txt
pip install -r script/monthly_ranking/requirements-test.txt
```

## Testing and Validation

Before submitting changes, run the relevant checks. The repository provides `scripts/validate.sh`, which runs the same checks used in CI:

```bash
# With the virtual environment activated or on PATH
PATH="$(pwd)/.venv/bin:$PATH" ./scripts/validate.sh
```

Individual checks can also be run:

```bash
# Python lint and format (ruff)
ruff check script/realtime_monitor/src script/realtime_monitor/tests \
  script/monthly_ranking/src script/monthly_ranking/tests
ruff format --check script/realtime_monitor/src script/realtime_monitor/tests \
  script/monthly_ranking/src script/monthly_ranking/tests

# Syntax check
python -m py_compile src/lambda_function.py src/lambda_exceptions.py

# Unit tests
PYTHONPATH=script/realtime_monitor/src python -m pytest script/realtime_monitor/tests
PYTHONPATH=script/monthly_ranking/src python -m pytest script/monthly_ranking/tests
```

For Terraform changes:

```bash
cd iac/realtime_monitor
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
```

The repository includes a `.pre-commit-config.yaml`. Run `pre-commit install` to enable the hooks locally.

## Coding Standards

- Use Python 3.12+ syntax.
- Add or update type hints where practical.
- Include tests that cover the changed behavior.
- Do not leave debug output or unrelated comments in commits.
- Never commit secrets such as Slack webhook URLs or AWS account IDs.

## Pull Request Process

1. Fork the repository and create a topic branch (e.g. `fix/short-description`).
2. Make focused, well-explained commits.
3. Ensure tests and `terraform validate` pass.
4. Open a pull request with a clear description of what changed and why.
5. Address review feedback; maintainers will merge when ready.

## Review Criteria

- The change behaves as intended and does not break existing behavior.
- Tests are added or updated as needed.
- Security-sensitive areas (IAM, URL validation, secret handling) are reviewed.
- Documentation is updated when behavior changes.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](./LICENSE).
