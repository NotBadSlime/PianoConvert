# Dark UI Readability Design

Date: 2026-06-26
Status: Approved by user

## Goal

Make the desktop app readable on Windows by changing the whole application chrome to a high-contrast dark interface.

## Problem

The current stylesheet sets some light backgrounds but leaves many text colors and disabled states to the system palette. On the user's machine, Qt renders several labels, controls, and disabled buttons as near-white text on a near-white background, making the right panel and toolbar hard to read.

## Decision

Use a full dark theme:

- Dark app background and panels.
- Light primary text for labels and active controls.
- Muted but visible text for disabled controls.
- Dark inputs with visible borders.
- Blue accent for primary states and checked controls.
- Keep the piano-roll grid dark, aligned with the app theme.

## Scope

Modify the PySide6 desktop UI only. Do not change transcription behavior, packaging, model logic, or release workflow.

## Verification

- Add a test that requires dark theme tokens to be present on `MainWindow`.
- Run the UI smoke test in offscreen mode.
- Render an offscreen screenshot to inspect readability.
- Run the full pytest suite.
