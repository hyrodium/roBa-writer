# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

roBa Writer is a Python CLI tool for automating firmware writing to Seeed xiao nrf52840-based split keyboards. The tool monitors USB connections and automatically writes firmware files when keyboards are put into bootloader mode.

## Project Structure

- `src/roBa_writer/__init__.py` - Main firmware writing tool with complete implementation including:
  - USB monitoring and device mounting/unmounting (Linux-specific using udisks2)
  - Firmware file extraction from ZIP files or directories
  - Interactive operation mode selection
  - Automated firmware writing workflow
- `pyproject.toml` - Project configuration with dependencies (psutil, click, ruff for development)
- `README.md` - Basic usage documentation
- `uv.lock` - Dependency lockfile

## Development Commands

This project uses uv for package management:

```bash
# Install dependencies
uv sync

# Run the firmware writing tool
uv run roBa-writer <firmware_directory_or_zip>

# Get help
uv run roBa-writer --help

# Lint code (if needed)
ruff check src/
```

## Tool Usage

The tool accepts either:
- A directory containing firmware files
- A ZIP file containing firmware files

Firmware file detection patterns:
- Reset firmware: Contains "reset" in filename
- Left keyboard: Contains "_L" or "-L" in filename
- Right keyboard: Contains "_R" or "-R" in filename

Operation modes:
1. Update right (main) device only
2. Update both devices (without reset)
3. Reset and update both devices

The automated workflow:
1. Detects and displays available firmware files
2. Prompts user to select operation mode
3. Monitors USB connections for new drives (with auto-mounting on Linux)
4. Writes firmware files based on selected mode
5. Handles USB reconnection and device removal detection

## Architecture Notes

- `OperationMode` enum defines three operation modes
- `DetectedFirmware` namedtuple holds firmware file paths
- `USBMonitor` class handles USB drive detection, mounting/unmounting (Linux-specific)
- `FirmwareExtractor` class handles ZIP file extraction and directory preparation
- `FirmwareWriter` class manages firmware file detection, validation and copying with detailed progress tracking
- `KeyboardProgrammer` class orchestrates the complete programming workflow with interactive mode selection
- Uses psutil for cross-platform USB drive detection
- Uses udisks2 (Linux) for automatic device mounting without sudo
- Uses click for command-line interface
- Implements timeout mechanisms and detailed error handling
- Supports both directory and ZIP file inputs with automatic cleanup