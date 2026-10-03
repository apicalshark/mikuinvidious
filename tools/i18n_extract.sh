#!/usr/bin/env bash
# Extract translatable strings to locales/messages.pot.
# Requires: uv run / pip install Babel
set -euo pipefail
cd "$(dirname "$0")/.."
uv run pybabel extract -F babel.cfg -o locales/messages.pot python templates static/themes/modern/js || pybabel extract -F babel.cfg -o locales/messages.pot python templates static/themes/modern/js
echo "wrote locales/messages.pot"
echo "merge into each locale with: msgmerge -U locales/<lang>/LC_MESSAGES/messages.po locales/messages.pot"
