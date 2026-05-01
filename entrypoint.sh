#!/bin/bash
set -e

ISSUE_URL="$1"
OPENAI_API_KEY="$2"
OPENAI_BASE_URL="$3"
OPENAI_MODEL="$4"
GITHUB_TOKEN="$5"
MODE="$6"
AGENT="$7"
SANDBOX="$8"
MAX_FILES="$9"
CREATE_PR="${10}"

# Set environment variables
export OPENAI_API_KEY="$OPENAI_API_KEY"
export OPENAI_BASE_URL="$OPENAI_BASE_URL"
export OPENAI_MODEL="$OPENAI_MODEL"
export GITHUB_TOKEN="$GITHUB_TOKEN"

# Build command
CMD="issue-fixer fix $ISSUE_URL --mode $MODE --max-files $MAX_FILES"

if [ "$AGENT" = "true" ]; then
    CMD="$CMD --agent"
fi

if [ "$SANDBOX" = "true" ]; then
    CMD="$CMD --sandbox"
fi

if [ "$CREATE_PR" = "false" ]; then
    CMD="$CMD --no-pr"
fi

echo "Running: $CMD"
eval "$CMD"

# Extract outputs
if [ "$CREATE_PR" = "true" ]; then
    # PR URL is printed in the output
    echo "Done! Check the output above for the PR URL."
fi
