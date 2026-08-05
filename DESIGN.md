---
name: "Skills Vector"
description: "A restrained paper, ink, and green system for private, auditable occupational intelligence."
colors:
  ink: "#18201c"
  muted: "#5f6a63"
  paper: "#f4f1e9"
  panel: "#fffdf8"
  line: "#d9d7ce"
  green: "#1c5a41"
  green-soft: "#e4f0e7"
  mint: "#cae8d4"
  amber: "#a76512"
  amber-soft: "#fff2d9"
  red: "#8d382e"
  red-soft: "#f8e8e4"
  blue: "#335d7e"
  blue-soft: "#e8f0f5"
  white: "#ffffff"
typography:
  display:
    fontFamily: "Georgia, serif"
    fontSize: "clamp(42px, 6vw, 76px)"
    fontWeight: 500
    lineHeight: 0.98
    letterSpacing: "-0.04em"
  headline:
    fontFamily: "Georgia, serif"
    fontSize: "34px"
    fontWeight: 500
    lineHeight: 1.1
  title:
    fontFamily: "Georgia, serif"
    fontSize: "26px"
    fontWeight: 500
    lineHeight: 1.1
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "13px"
    fontWeight: 700
    lineHeight: 1.55
  eyebrow:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "11px"
    fontWeight: 800
    lineHeight: 1.55
    letterSpacing: "0.14em"
rounded:
  none: "0"
  sm: "4px"
  md: "6px"
  pill: "20px"
  circle: "50%"
spacing:
  xs: "5px"
  sm: "8px"
  control-y: "10px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  "2xl": "24px"
  "3xl": "28px"
  "4xl": "34px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.white}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "10px 14px"
  button-primary-hover:
    backgroundColor: "{colors.green}"
    textColor: "{colors.white}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "10px 14px"
  button-text:
    backgroundColor: "transparent"
    textColor: "{colors.green}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "10px 14px"
  button-text-hover:
    backgroundColor: "{colors.green-soft}"
    textColor: "{colors.green}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "10px 14px"
  input:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    padding: "10px"
  tab:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    padding: "12px 16px"
  tab-active:
    backgroundColor: "transparent"
    textColor: "{colors.green}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "12px 16px"
  role-card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "26px"
  status-success:
    backgroundColor: "{colors.mint}"
    textColor: "{colors.green}"
    rounded: "{rounded.pill}"
    padding: "4px 8px"
  reason-chip:
    backgroundColor: "{colors.blue-soft}"
    textColor: "{colors.blue}"
    rounded: "{rounded.pill}"
    padding: "3px 6px"
  graph-node:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "12px"
  graph-node-selected:
    backgroundColor: "{colors.green-soft}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "12px"
  cluster-row-active:
    backgroundColor: "{colors.green-soft}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "12px 10px"
---

# Design System: Skills Vector

## Overview

**Creative North Star: "The Private Evidence Ledger"**

Skills Vector uses a restrained paper, ink, and green world that feels closer to a carefully maintained working record than a generic analytics dashboard. Warm paper surrounds near-white panels; dark ink carries the content; green is reserved for identity, action, selection, and validated evidence.

The interface is dense only where inspection requires it. Serif headings establish editorial hierarchy, while system sans-serif text, fine borders, compact controls, and progressive disclosure keep the run, claim, finding, and source trail operational. Uncertainty, disagreement, failure, and approval eligibility remain visually explicit rather than being softened into decoration.

**Key Characteristics:**

- Warm paper canvas with restrained bordered panels
- Editorial serif orientation paired with compact sans-serif operation
- Semantic color reserved for evidence relationships and run state
- Progressive disclosure from brief to graph, findings, sources, and raw payloads
- Human approval presented as a visible gate, never an automatic outcome

## Colors

The palette is mostly warm neutral, with evidence green as the primary voice and blue, amber, and red reserved for narrow semantic jobs.

### Primary

- **Evidence Green:** Identity mark, eyebrows, active tabs, trace links, selected graph nodes, validated states, and primary-button hover.
- **Soft Evidence Green:** Low-emphasis hover and selected surfaces, including text buttons, active evidence clusters, validated chips, and approved containers.
- **Approval Mint:** Stronger success fill for approved and succeeded status pills.

### Secondary

- **Reason Blue:** Ranking-reason text inside evidence chips.
- **Soft Reason Blue:** Ranking-reason chip fill; it does not compete with primary action green.

### Tertiary

- **Awaiting Amber and Soft Amber:** Awaiting approval, partial drafts, warnings, and the graph legend for human action.
- **Failure Red and Soft Red:** Failed or blocked stages, unsupported claims, disagreement, and blocked approval.

### Neutral

- **Ink:** Primary text, primary-button fill, and dark raw-payload surfaces.
- **Muted Ink:** Secondary copy, metadata, timestamps, counts, and helper labels.
- **Paper:** The page canvas and translucent sticky header base.
- **Panel:** Role cards and the run workspace.
- **Line:** Fine separators, borders, tab rails, and graph-adjacent structure.
- **White:** Controls and graph nodes that require a clean working surface.

### Named Rules

**The Semantic Color Rule.** Green means action, selection, or validated success; amber means awaiting or partial; red means blocked, failed, or disputed; blue is reserved for evidence-ranking reasons.

## Typography

**Display Font:** Georgia (with serif fallback)

**Body Font:** Inter (with ui-sans-serif and system fallbacks)

**Label/Mono Font:** The body stack handles controls and labels; run identifiers and raw payloads use ui-monospace, SFMono-Regular, Consolas, and monospace.

**Character:** Georgia gives the brief and workspace an editorial, judgment-oriented voice. The sans-serif stack keeps controls, metadata, evidence, and operational detail compact and direct; Inter is preferred when available but is not separately loaded by the interface.

### Hierarchy

- **Display** (500, `clamp(42px, 6vw, 76px)`, 0.98): The landing statement only, with tight tracking and a green italic continuation.
- **Headline** (500, 34px, 1.1): Section and workspace titles.
- **Title** (500, 26px, 1.1): Role-card names; smaller Georgia titles at 22px identify brief sections and stage details.
- **Body** (400, 15px, 1.55): Primary explanatory copy. Claims and details generally stay within 68–80 characters per line.
- **Label** (700, 13px): Buttons and compact interaction labels.
- **Eyebrow** (800, 11px, 0.14em letter spacing, uppercase): Geography, monitor, phase, and category orientation.
- **Micro metadata** (9–12px): Statuses, node metrics, trace labels, source bylines, and ranking context.

### Named Rules

**The Serif Orientation Rule.** Use Georgia to orient the reader to sections and decisions; use sans-serif for operation, evidence detail, controls, and state.

## Layout

The page uses a centered 1240px maximum width with 34px side padding, reduced to 16px on narrow phones. A 70px sticky top bar anchors identity and privacy. The opening hero is a horizontal composition with a 60px gap and a minimum height of 380px; its circular role count disappears below 850px.

The role chooser is a three-column grid with 16px gutters. The run workspace is a bordered panel with 34px internal padding and 70px top separation. Brief sections use a two-column grid with 16px gutters, while designated wide sections span both columns. Trace totals use six equal cells; approval fields use a compact `1fr 2fr auto` grid.

The workflow graph preserves its seven fixed phases at a 1060px minimum width and scrolls horizontally rather than compressing its nodes. Evidence exploration uses a 260px cluster rail beside a flexible results column, reducing the rail to 220px below 1000px.

At 1000px, trace totals become three columns. At 850px, role cards and brief sections become single-column, approval and detail forms stack, claim coverage moves below claim copy, the evidence rail is replaced by a cluster select, and explanatory header rows stack. At 520px, workspace padding becomes 20px, tabs scroll horizontally, trace totals become two columns, evidence filters become full-width rows, reason chips align left, and the footer stacks.

## Elevation & Depth

The system is flat by default. Fine borders, paper-to-panel tonal contrast, graph connectors, and inset state fills establish structure; elevation appears only for the sticky header, the open workspace, a hovered role card, a selected graph node, and the small privacy signal.

### Shadow Vocabulary

- **Workspace / Card Lift** (`0 20px 60px rgba(36,44,38,.10)`): Permanent depth for the open workspace and temporary lift on role-card hover.
- **Selected Node Lift** (`0 8px 22px rgba(28,90,65,.11)`): A contained green-tinted shadow for the active workflow node.
- **Privacy Signal Glow** (`0 3px 10px rgba(94,169,121,.35)`): A small ambient glow around the local/private status dot.
- **Sticky Header Blur** (`backdrop-filter: blur(12px)`): Keeps the 92%-opaque paper header legible over scrolling content.

Role cards move upward 4px over 200ms on hover; graph-node state changes run over 180ms. Loading placeholders use a 1.4s horizontal shimmer. Smooth scrolling supports workspace, graph-detail, and evidence transitions, while the reduced-motion query collapses animation and transition durations to 0.01ms and disables smooth scrolling.

### Named Rules

**The Flat-by-Default Rule.** Keep resting content on bordered, tonal surfaces; add shadow only when a workspace, hover, selection, or privacy signal needs explicit depth.

## Shapes

Most operational containers are square or nearly square. Brief sections, graph nodes, inputs, notices, evidence rows, and the workspace rely on 1px borders without added rounding. Role cards use a restrained 6px corner, while buttons and loading bars use 4px corners.

Pills use a 20px radius for status, validation, coverage, and ranking-reason chips. True circles are reserved for the 34px brand mark, the 150px monitored-role note, and the 8px privacy dot. Empty states use a dashed border; graph phases use thin straight connectors and rectangular nodes so the workflow reads as a controlled DAG rather than a decorative diagram.

## Components

### Buttons

- **Shape:** Compact rectangular controls with gently curved 4px corners and `10px 14px` padding.
- **Primary:** Ink fill, white text, 13px bold label; hover changes the fill to evidence green.
- **Text:** Transparent surface with green text; hover adds a soft-green fill. Trace and detail links use the same green emphasis with minimal or no container.
- **Focus / Disabled:** All controls receive a 3px translucent green focus outline with 2px offset. Disabled buttons use 48% opacity and a not-allowed cursor.

### Inputs / Fields

Inputs and selects are square white fields with a 1px line border and 10px padding. Labels are muted and compact; evidence-filter labels add uppercase tracking. Focus uses the shared green outline. On narrow layouts, evidence filters become a one-column grid and selects fill the available width.

### Tabs

Run views sit on a 1px bottom rail. Tabs are transparent with muted text and `12px 16px` padding; hover darkens the label, while the active tab becomes bold green with a 2px green underline. Arrow keys, Home, and End move between tabs. The tab strip scrolls horizontally below 520px.

### Status and Coverage Chips

Status chips are compact 20px pills. Neutral states use a quiet warm-gray fill; succeeded and approved use mint with green text; awaiting and partial-approval states use soft amber; failed and blocked states use soft red. Claim coverage chips use neutral fill until validated or blocked. Status text is capitalized, while small graph and cluster states use tracked uppercase.

### Role Cards / Containers

Role cards use the panel surface, a 1px line border, 6px corners, 26px padding, and a 340px minimum height. The role index and description lead to a bottom-aligned metadata divider and action row. Hover lifts the card 4px, applies the large ambient shadow, and darkens the border. Brief sections remain square, flat, bordered containers with 22px padding.

### Graph Nodes

Workflow nodes are full-width rectangular buttons with a 96px minimum height, white fill, 1px border, and 12px padding. Status, stage name, and invocation/source/timing metrics form a compact vertical stack. Selection switches to a green border and soft-green surface with a contained shadow; failed and partial nodes use red or amber borders and fills. Phase connectors remain thin gray lines behind the nodes.

### Evidence Clusters

Cluster rows are full-width, borderless buttons separated by top rules. Each row balances the cluster label and finding count against source count, then places validation state below. Hover and active states add a soft-green surface and increase horizontal padding from 4px to 10px. On small screens, the rail disappears and the active cluster moves into a select control.

### Reason Chips

Ranking reasons appear as small blue-on-soft-blue pills with 3px by 6px padding. They wrap and align to the end on wide layouts, then move left when source headings stack below 520px. Their blue color is exclusive to ranking explanation.

### Source Rows

Each source is a flat row with 22px vertical padding and a bottom rule. Category and reason chips lead; the green underlined title links to the public source; muted publisher/date metadata, excerpt, and provenance follow. Connected findings sit on a slightly darker neutral inset. Provenance and worker payloads stay collapsed in native details elements until requested, and source lists paginate in groups of 25.

## Do's and Don'ts

### Do:

- **Do** keep the warm paper canvas, near-white panels, dark ink, fine borders, and restrained evidence green as the dominant visual world.
- **Do** preserve semantic state mapping across badges, claims, graph nodes, approval panels, and evidence clusters.
- **Do** keep uncertainty, disagreement, failures, provenance, and approval eligibility visually explicit.
- **Do** preserve progressive disclosure from brief to evidence cluster, source, connected finding, and raw payload.
- **Do** retain horizontal graph scrolling and the mobile cluster select when space is constrained.

### Don't:

- **Don't** use green, amber, red, or blue as interchangeable decoration; each color has an implemented information role.
- **Don't** add broad rounding, gradients, or persistent card shadows to resting operational surfaces.
- **Don't** compress the seven-phase graph until labels or metrics become unreadable.
- **Don't** hide partial or failed work behind an empty state when inspectable trace data exists.
- **Don't** present approval as publication, automation, or a completed action before the human gate is satisfied.
