#!/usr/bin/env bash
# Run the same validation steps used in CI locally.
# Assumes a virtual environment with dependencies is activated.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> ruff check"
ruff check script/realtime_monitor/src script/realtime_monitor/tests script/monthly_ranking/src script/monthly_ranking/tests

echo "==> ruff format --check"
ruff format --check script/realtime_monitor/src script/realtime_monitor/tests script/monthly_ranking/src script/monthly_ranking/tests

echo "==> mypy (realtime)"
PYTHONPATH=script/realtime_monitor/src mypy script/realtime_monitor/src

echo "==> mypy (monthly_ranking)"
PYTHONPATH=script/monthly_ranking/src mypy script/monthly_ranking/src

echo "==> bandit"
bandit -r script/realtime_monitor/src script/monthly_ranking/src

echo "==> pip-audit"
pip-audit -r script/realtime_monitor/requirements.txt -r script/monthly_ranking/requirements.txt

echo "==> pytest (realtime)"
PYTHONPATH=script/realtime_monitor/src python -m pytest \
  --cov=script/realtime_monitor/src \
  --cov-report=term-missing \
  script/realtime_monitor/tests

echo "==> pytest (monthly_ranking)"
PYTHONPATH=script/monthly_ranking/src python -m pytest \
  --cov=script/monthly_ranking/src \
  --cov-report=term-missing \
  script/monthly_ranking/tests

echo "==> terraform fmt -check"
terraform fmt -check -recursive iac/

echo "==> terraform validate (realtime_monitor)"
(
  cd iac/realtime_monitor
  terraform init -backend=false -input=false >/dev/null
  terraform validate
)

echo "==> terraform validate (monthly_ranking)"
(
  cd iac/monthly_ranking
  terraform init -backend=false -input=false >/dev/null
  terraform validate
)

echo "==> checkov (Terraform)"
checkov -d iac/ --framework terraform --quiet --compact

echo "==> All validation checks passed."
