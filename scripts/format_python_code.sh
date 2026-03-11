#!/bin/bash

# Code formatting script for Python files
#
# Usage:
#   ./scripts/format_python_code.sh [options] <paths...>
#
# - autopep8 and ruff run on all provided paths (required, can be directories)
# - ty only runs on specific .py files; if no .py files in paths,
#   auto-detects changed files on current branch vs webdev
#
# Options:
#   --skip-ty    Skip type checking
#   --strict     Fail on ty warnings (not just errors)

set -e  # Exit on any error

# Directories to exclude from ty checking
TY_EXCLUDE_DIRS=(
    "notebooks/"
)

SKIP_TY=false
STRICT=false
PATHS=""
TY_FILES=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-ty)
            SKIP_TY=true
            shift
            ;;
        --strict)
            STRICT=true
            shift
            ;;
        *)
            PATHS="$PATHS $1"
            # Collect .py files for ty - expand directories
            if [[ "$1" == *.py ]] && [ -f "$1" ]; then
                TY_FILES="$TY_FILES $1"
            elif [ -d "$1" ]; then
                # Find all .py files in the directory
                DIR_PY_FILES=$(find "$1" -name "*.py" -type f 2>/dev/null)
                TY_FILES="$TY_FILES $DIR_PY_FILES"
            fi
            shift
            ;;
    esac
done

PATHS=$(echo "$PATHS" | xargs)

# Run autopep8 and ruff
uv run --frozen autopep8 --recursive --in-place $PATHS
uv run --frozen ruff check $PATHS --select I001 --fix --quiet
uv run --frozen ruff check $PATHS --quiet --fix

# For ty: if no .py files provided, auto-detect from git
if [ "$SKIP_TY" = false ]; then
    if [ -z "$(echo "$TY_FILES" | xargs)" ]; then
        # Use origin/main to include uncommitted changes
        CHANGED_FILES=$(git diff --name-only origin/main -- '*.py')

        if [ -z "$CHANGED_FILES" ]; then
            echo "No Python files changed on current branch vs webdev - skipping ty"
            exit 0
        fi

        # Collect existing changed files
        for file in $CHANGED_FILES; do
            [ -f "$file" ] && TY_FILES="$TY_FILES $file"
        done
    fi

    # Filter out excluded directories (single pass)
    FILTERED_TY_FILES=""
    for file in $TY_FILES; do
        exclude=false
        for dir in "${TY_EXCLUDE_DIRS[@]}"; do
            [[ "$file" == "$dir"* ]] && exclude=true && break
        done
        [ "$exclude" = false ] && FILTERED_TY_FILES="$FILTERED_TY_FILES $file"
    done
    TY_FILES=$(echo "$FILTERED_TY_FILES" | xargs)

    if [ -n "$TY_FILES" ]; then
        echo "Running ty check on $(echo "$TY_FILES" | wc -w | xargs) file(s)..."

        if [ "$STRICT" = true ]; then
            # Strict mode: fail on warnings too
            uv run --frozen ty check --error-on-warning $TY_FILES
        else
            # Normal mode: only fail on errors, show warnings
            uv run --frozen ty check $TY_FILES
        fi
    fi
fi
