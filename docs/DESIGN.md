---
name: Adaptive Skills
description: A precise local operations console for managing Agent Skills.
colors:
  night-catalog: "#0d100f"
  sidebar-forest: "#101412"
  workbench-surface: "#151a18"
  raised-surface: "#1a211e"
  active-surface: "#202824"
  primary-text: "#edf4ef"
  secondary-text: "#98a59d"
  quiet-text: "#6f7b74"
  operational-mint: "#77e0af"
  action-mint: "#49ca91"
  caution-amber: "#f2b866"
  danger-red: "#ff7d79"
  information-blue: "#85b9f8"
typography:
  headline:
    fontFamily: "Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, SF Pro Display, Segoe UI, sans-serif"
    fontSize: "23px"
    fontWeight: 630
    lineHeight: 1.2
    letterSpacing: "-0.03em"
  title:
    fontFamily: "Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, SF Pro Display, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 620
    lineHeight: 1.3
  body:
    fontFamily: "Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, SF Pro Display, Segoe UI, sans-serif"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, SF Pro Display, Segoe UI, sans-serif"
    fontSize: "10px"
    fontWeight: 650
    lineHeight: 1.4
    letterSpacing: "0.08em"
rounded:
  control: "9px"
  compact-surface: "11px"
  panel: "15px"
  hero: "18px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "13px"
  lg: "19px"
  page: "32px"
components:
  button-primary:
    backgroundColor: "{colors.action-mint}"
    textColor: "{colors.night-catalog}"
    rounded: "{rounded.control}"
    padding: "0 14px"
    height: "36px"
  button-secondary:
    backgroundColor: "{colors.active-surface}"
    textColor: "{colors.primary-text}"
    rounded: "{rounded.control}"
    padding: "0 14px"
    height: "36px"
  panel:
    backgroundColor: "{colors.workbench-surface}"
    textColor: "{colors.primary-text}"
    rounded: "{rounded.panel}"
    padding: "20px"
  input:
    backgroundColor: "{colors.sidebar-forest}"
    textColor: "{colors.primary-text}"
    rounded: "{rounded.control}"
    padding: "0 10px"
    height: "38px"
---

# Design System: Adaptive Skills

## Overview

**Creative North Star: "The Local Operations Bench"**

Adaptive Skills should feel like a quiet, instrumented workbench for filesystem and catalog operations. The dark green-tinted surfaces support extended expert use, while mint is reserved for selected state, verified health, and primary actions. Information density is intentional, but every row must retain a clear owner, state, and next action.

The system rejects generic “AI magic” dashboards, decorative analytics, ambiguous mutation controls, and cloud-first account patterns. Familiar desktop affordances are preferred over novel interaction patterns; motion communicates state and never delays work.

**Key Characteristics:**

- Dark, green-tinted operational surfaces with restrained mint accents.
- Compact hierarchy with explicit provenance and filesystem paths.
- Full-border state callouts and badges, never decorative side stripes.
- Predictable sidebar, list, detail, and confirmation patterns.
- Status language reinforced by icons and text, not color alone.

## Colors

The palette uses near-black forest neutrals as the workspace and a controlled mint signal for ownership, health, and action.

### Primary

- **Operational Mint:** Primary actions, active navigation, verified state, and focus indication.
- **Action Mint:** Stronger mint reserved for selected controls and action endpoints.

### Secondary

- **Caution Amber:** Drift, conflict, risk acknowledgement, and actions requiring review.
- **Danger Red:** Failed operations and destructive boundaries.
- **Information Blue:** Secondary informational signals that must remain distinct from success.

### Neutral

- **Night Catalog:** Application background and deepest canvas.
- **Sidebar Forest:** Navigation and recessed inputs.
- **Workbench Surface:** Default panels and rows.
- **Raised Surface:** Hovered, selected, or progressively disclosed content.
- **Primary Text, Secondary Text, Quiet Text:** Three explicit hierarchy levels; quiet text is never used for critical instructions.

### Named Rules

**The Operational Mint Rule.** Mint communicates selection, health, focus, or an available primary action. It is never decoration.

**The Two-Signal Rule.** Warning, danger, success, and external ownership always use both semantic text or an icon and color.

## Typography

**Display Font:** Inter with the native macOS and Segoe UI fallback stack.
**Body Font:** Inter with the native system fallback stack.
**Label/Mono Font:** SFMono-Regular with Consolas fallback for paths, identifiers, and hashes.

**Character:** A compact native product voice. Weight and tone create hierarchy; decorative type pairing is prohibited.

### Hierarchy

- **Headline** (630, 23px, 1.2): Page identity and major detail titles.
- **Title** (620, 15px, 1.3): Panel and workflow section titles.
- **Body** (400, 11px, 1.6): Instructions and explanatory copy, capped near 70 characters where prose is uninterrupted.
- **Label** (650, 10px, 0.08em): Eyebrows and compact state labels; uppercase is limited to stable section metadata.
- **Mono** (400, 8–10px): Filesystem paths, commit hashes, identifiers, and command-adjacent data.

### Named Rules

**The Path Is Data Rule.** Paths and identifiers use mono styling, truncation, and a full-value tooltip; they never masquerade as prose.

## Elevation

The interface is flat by default and uses tonal layering plus fine full borders for structure. Ambient shadows belong only to the hero, transient overlays, and hovered interactive cards; ordinary rows do not float.

### Shadow Vocabulary

- **Hero Ambient:** A broad low-opacity shadow for the overview hero only.
- **Interactive Lift:** A restrained shadow paired with a 1–2px transform on hoverable catalog cards.
- **Overlay Depth:** A stronger shadow for confirmation dialogs and drawers that must separate from the workspace.

### Named Rules

**The Flat Workbench Rule.** Operational lists remain on the workbench plane. Elevation is a response to interaction or modality, not a default card treatment.

## Components

### Buttons

- **Shape:** Compact gently curved controls (9px radius).
- **Primary:** Mint action surface, dark text, 36px default height, used once per local decision group.
- **Hover / Focus:** A 1px vertical lift on hover and a visible 2px mint focus outline; reduced-motion removes the lift.
- **Secondary / Ghost / Warning:** Tonal surface, transparent surface, and amber acknowledgement variants reuse the same geometry.

### Chips

- **Style:** Small pill with a tonal background, full border, semantic label, and compact weight.
- **State:** Selected or risk states change text, background, and border together; color is never the sole difference.

### Cards / Containers

- **Corner Style:** Panels use 15px, compact rows use 9–11px.
- **Background:** Workbench Surface by default; Raised Surface only for interaction or progressive disclosure.
- **Shadow Strategy:** Flat at rest; see the Elevation rules.
- **Border:** A subtle full perimeter border; colored side-stripe accents are prohibited.
- **Internal Padding:** 13–21px according to density and hierarchy.

### Inputs / Fields

- **Style:** Recessed Sidebar Forest surface, fine border, 8–9px radius, and explicit labels.
- **Focus:** Visible mint outline plus border shift, never color-only placeholder changes.
- **Error / Disabled:** Error includes a message and danger treatment; disabled controls retain readable labels at reduced opacity.

### Navigation

The fixed sidebar uses icon, title, description, and current-page state. Active navigation receives a restrained mint-tinted surface and border. Hover is tonal only; motion stays within 150–170ms and is disabled when reduced motion is requested.

### Ownership Rows

System-managed, Adaptive-managed, and external-existing entries share one row geometry but use explicit badges, ownership copy, and distinct allowed actions. External rows never expose a destructive unlink control until ownership is transferred.

## Do's and Don'ts

### Do:

- **Do** name the exact Agent, directory, ownership state, and filesystem effect before a mutation.
- **Do** preserve the existing 9–15px component radius, compact 8–13px spacing rhythm, and restrained mint vocabulary.
- **Do** show system projects, managed links, and external content as distinct states with text and icons.
- **Do** keep all controls keyboard reachable with a visible 2px focus outline and WCAG AA contrast.
- **Do** respect reduced-motion preferences by removing lifts and nonessential transitions.

### Don't:

- **Don't** create generic “AI magic” dashboards that conceal provenance or imply the model is always correct.
- **Don't** add decorative analytics, excessive cards, or motion that competes with catalog and filesystem state.
- **Don't** use ambiguous install, delete, replace, or unlink actions that fail to distinguish Adaptive-managed content from external content.
- **Don't** introduce cloud-first account patterns that undermine the local-first trust boundary.
- **Don't** use colored side stripes, gradient text, decorative glass panels, or color-only statuses.
