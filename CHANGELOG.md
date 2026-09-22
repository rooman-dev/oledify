# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-22

First release.

### Added

- True-black crush with an adjustable threshold and smooth falloff
- Live before/after preview with a draggable split line
- Output presets (4K, QHD, 2K, FHD, Ultrawide, Phone QHD+, Phone FHD+, iPhone Pro Max) and a custom size
- Three fit modes: crop to fill, black bars and blurred fill
- Per-image crop focus, set by clicking the preview
- Batch mode: add files or a folder and export every image to every selected size, with progress and cancel
- Debanding for dark gradients
- PNG, JPEG and WebP output with a quality setting, never overwriting existing files
- Upscale warning when a preset is larger than the source image
- Settings remembered between runs, plus a Reset to defaults button
- Windows installer (per-user, no administrator rights) and a portable zip
