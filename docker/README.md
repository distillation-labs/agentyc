# Docker Setup For agentyc

This directory contains Docker build assets for the agentyc MCP browser runtime.

## Quick Start

Build the standard image:

```bash
docker build -t agentyc .
```

Build the fast image variant:

```bash
docker build -f Dockerfile.fast -t agentyc .
```

If you use the layered base-image workflow, build those base images first:

```bash
./scripts/build-base-images.sh
```

## Files

- `Dockerfile`: self-contained build.
- `Dockerfile.fast`: build that expects prebuilt base images.
- `docker/base-images/system/`: Python and system dependencies.
- `docker/base-images/chromium/`: Chromium-enabled base image.
- `docker/base-images/python-deps/`: Python dependency layer.
- `scripts/build-base-images.sh`: helper for building the base layers.

## Notes

- Docker support is for packaging and running the MCP browser runtime.
- Shared-browser workflows still depend on exposing a CDP endpoint if you want external MCP server processes to attach.
- Public runtime behavior is defined by the root package modules, not by older Browser-Use-era wording in historical material.
