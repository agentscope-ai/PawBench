# -*- coding: utf-8 -*-
"""Shared constants for all pawbench agent implementations."""

# Standard workspace path inside every benchmark container.
AGENT_WORKSPACE = "/app/working/workspaces/default"

# Base image for all Harbor-bridge agents (Python 3.12 + harbor-framework).
# Build: docker build -f docker/Dockerfile.pawbench-base -t pawbench-base:latest .
PAWBENCH_BASE_IMAGE    = "pawbench-base:latest"           # docker/Dockerfile.pawbench-base
