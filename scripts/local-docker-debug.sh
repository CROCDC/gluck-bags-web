#!/usr/bin/env bash
set -euo pipefail

# Build and run the stack locally, tailing logs.
docker compose up --build
