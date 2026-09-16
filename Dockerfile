# Multi-stage Dockerfile for Donetick MCP Server

FROM python:3.13-slim AS base

# Set working directory
WORKDIR /app

# Copy project metadata and application code (setuptools needs src/ and README.md to build)
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install the package and its dependencies (all dependencies ship prebuilt wheels)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Create non-root user for security
RUN useradd -m -u 1000 mcpuser && \
    chown -R mcpuser:mcpuser /app

# Switch to non-root user
USER mcpuser

# Set Python environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Expose port (only needed if using HTTP transport)
# EXPOSE 3000

# Health check (optional, for monitoring)
# HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
#   CMD python -c "import sys; sys.exit(0)"

# Run the MCP server with stdio transport
CMD ["python", "-m", "donetick_mcp.server"]
