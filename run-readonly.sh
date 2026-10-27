#!/bin/sh
# Launch Omen Control Centre straight from this folder, read-only.
# Nothing is installed and nothing can be written to the keyboard/fan driver.
cd "$(dirname "$0")" || exit 1
OMEN_READ_ONLY=1 exec python3 src/omen-centre.py
