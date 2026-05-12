#!/usr/bin/env bash
# Installer for gogol-school-ai role assistants.
# Usage: curl -fsSL <url>/install.sh | bash -s <role-slug>
#
# TODO: пока заглушка. Допишется, когда финализируем структуру роли.

set -euo pipefail

ROLE="${1:-}"
if [[ -z "$ROLE" ]]; then
  echo "Usage: install.sh <role-slug>"
  echo "Available roles: doc-fin-ops, client-office-ops, senior-admin,"
  echo "                 marketing-assistant, smm, brand-pr, product-manager,"
  echo "                 student-comms, product-assistant"
  exit 1
fi

echo "Установка роли: $ROLE"
echo "(скрипт пока в разработке — реальная установка появится после пилота doc-fin-ops)"
