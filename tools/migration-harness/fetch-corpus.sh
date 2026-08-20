#!/usr/bin/env bash
# Clone the Tier-1 corpus (openfga/sample-stores) into ./corpus/.
set -euo pipefail
cd "$(dirname "$0")"
if [ -d corpus/sample-stores ]; then
  git -C corpus/sample-stores pull --ff-only
else
  mkdir -p corpus
  git clone --depth 1 https://github.com/openfga/sample-stores.git corpus/sample-stores
fi
echo "stores available:"
ls corpus/sample-stores/stores
