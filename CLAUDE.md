# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

roBa Writer is a Python tool for automating firmware writing to Seeed xiao nrf52840-based split keyboards. The tool monitors USB connections and automatically writes firmware files when keyboards are put into bootloader mode.

## Project Structure

- `src/roBa_writer/main.py` - Main firmware writing tool with USB monitoring and file copying functionality
- `src/roBa_writer/__init__.py` - Package initialization with exports
- `pyproject.toml` - Project configuration with dependencies (psutil, click)

## Development Commands

This project uses uv for package management:

```bash
# Install dependencies
uv sync

# Run the firmware writing tool
uv run roBa-writer <firmware_directory_or_zip>

# Skip reset firmware writing (keyboard firmware only) 
uv run roBa-writer <firmware_directory_or_zip> --skip-reset

# Get help
uv run roBa-writer --help
```

## Tool Usage

The tool accepts either:
- A directory containing firmware files
- A ZIP file containing firmware files

Required firmware files:
- `roBa_L-seeeduino_xiao_ble-zmk.uf2` (Left keyboard firmware)
- `roBa_R-seeeduino_xiao_ble-zmk.uf2` (Right keyboard firmware)  
- `settings_reset-seeeduino_xiao_ble-zmk.uf2` (Reset firmware, shared)

The automated workflow:
1. Monitors USB connections for new drives
2. Writes reset firmware when keyboard enters bootloader mode
3. Waits for USB reconnection
4. Writes keyboard-specific firmware
5. Repeats process for both left and right keyboards

## Architecture Notes

- `USBMonitor` class handles USB drive detection and monitoring
- `FirmwareExtractor` class handles ZIP file extraction and directory preparation
- `FirmwareWriter` class manages firmware file validation and copying
- `KeyboardProgrammer` class orchestrates the complete programming workflow
- Uses psutil for cross-platform USB drive detection
- Uses click for command-line interface
- Implements timeout mechanisms for user interactions
- Supports both directory and ZIP file inputs with automatic cleanup