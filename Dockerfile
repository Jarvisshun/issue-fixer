FROM python:3.12-slim

LABEL maintainer="Jarvisshun"
LABEL description="Issue Fixer - AI Agent that auto-fixes GitHub Issues"

# Install git (needed for repo cloning)
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy package files first for better caching
COPY pyproject.toml README.md LICENSE ./
COPY issue_fixer/ issue_fixer/

# Install dependencies
RUN pip install --no-cache-dir -e .

# Copy entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
