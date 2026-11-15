# Image for scripts/screenshots.py (Phase 6, C9): Playwright's official Python image (browsers
# included, arm64 build) plus the matching Python package, which the image doesn't ship.
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble@sha256:330d401ae23c905896a0c26c66c36fc971b621519e7b11b435a8328d7bf6e031
RUN pip install --no-cache-dir playwright==1.64.1
