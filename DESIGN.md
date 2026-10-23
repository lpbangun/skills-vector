---
name: Skills Vector
description: Private-first occupational intelligence — Evidence Atlas Control design system
colors:
  present: "#1a4f8c"
  present-bright: "#3d78bc"
  present-soft: "#d6e4f2"
  present-ink: "#123a6a"
  uncertain: "#8a5c48"
  uncertain-soft: "#efe6e0"
  disagree: "#8e2f45"
  disagree-soft: "#f0d9df"
  analysis: "#5c4a78"
  analysis-soft: "#ebe6f2"
  navy: "#0c1828"
  navy-mid: "#163049"
  graphite: "#3f4a55"
  graphite-soft: "#5c6772"
  mineral: "#f2f5f7"
  mineral-deep: "#e4ebf0"
  pale: "#c9d5df"
  paper: "#fafbfc"
  rule: "#163049"
  rule-soft: "#bcc8d4"
  white: "#ffffff"
  focus: "#1a4f8c"
typography:
  display:
    fontFamily: "STIX Two Text, Times New Roman, Times, serif"
    fontSize: "clamp(1.85rem, 4.2vw, 2.85rem)"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "STIX Two Text, Times New Roman, Times, serif"
    fontSize: "clamp(1.45rem, 3vw, 1.95rem)"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  title:
    fontFamily: "STIX Two Text, Times New Roman, Times, serif"
    fontSize: "1.2rem"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.01em"
  finding:
    fontFamily: "STIX Two Text, Times New Roman, Times, serif"
    fontSize: "1.05rem"
    fontWeight: 500
    lineHeight: 1.45
  body:
    fontFamily: "IBM Plex Sans, Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "IBM Plex Mono, Courier New, Courier, monospace"
    fontSize: "0.6875rem"
    fontWeight: 500
    lineHeight: 1.35
    letterSpacing: "0.08em"
rounded:
  none: "0px"
  sm: "2px"
  control: "0px"
spacing:
  xs: "0.35rem"
  sm: "0.6rem"
  md: "1rem"
  lg: "1.5rem"
  xl: "2.25rem"
  "2xl": "3.5rem"
components:
  button-primary:
    backgroundColor: "{colors.navy}"
    textColor: "{colors.white}"
    rounded: "{rounded.none}"
    padding: "0.55rem 0.9rem"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.navy-mid}"
    textColor: "{colors.white}"
  button-present:
    backgroundColor: "{colors.present}"
    textColor: "{colors.white}"
    rounded: "{rounded.none}"
    padding: "0.55rem 0.9rem"
    height: "44px"
  button-present-hover:
    backgroundColor: "{colors.present-ink}"
    textColor: "{colors.white}"
  button-secondary:
    backgroundColor: "{colors.white}"
    textColor: "{colors.navy}"
    rounded: "{rounded.none}"
    padding: "0.55rem 0.9rem"
    height: "44px"
  button-disagree:
    backgroundColor: "{colors.disagree}"
    textColor: "{colors.white}"
    rounded: "{rounded.none}"
    padding: "0.55rem 0.9rem"
    height: "44px"
  callout-uncertainty:
    backgroundColor: "{colors.uncertain-soft}"
    textColor: "{colors.navy-mid}"
    rounded: "{rounded.none}"
    padding: "0.75rem 0.9rem"
  callout-disagreement:
    backgroundColor: "{colors.disagree-soft}"
    textColor: "{colors.navy-mid}"
    rounded: "{rounded.none}"
    padding: "0.75rem 0.9rem"
  rail-active:
    backgroundColor: "{colors.present-soft}"
    textColor: "{colors.present-ink}"
    rounded: "{rounded.none}"
    padding: "0.5rem 0.45rem"
---

# Design System: Skills Vector

## Overview

**Creative North Star: "The Evidence Desk"**

Skills Vector looks like a private research observatory for occupational intelligence — not a clinical dashboard, not a SaaS marketing shell, and not a playful ops toy. Surfaces read as mineral paper and navy ink; color encodes evidence state (present, uncertain, disagree) rather than decoration. The locked reference is **Evidence Atlas · Control**.

Density is deliberate: comparison tables, coverage matrices, provenance stages, and brief sections sit close when related and open up between major regions. Visualizations lead comprehension; prose supports them. Human approval remains an explicit gate, never a soft status chip.

**Key Characteristics:**
- Research desk / atlas observatory, light mineral canvas
- Cobalt present · clay uncertainty · wine disagreement (no teal, no amber-yellow)
- STIX Two Text for findings · IBM Plex Sans for UI · IBM Plex Mono for data
- Flat tonal depth; square corners; full borders or top hairlines — never thick side-tab rails
- Presence / absence / disagreement honesty — no fake coverage percentages

**Canonical reference:** `design-concepts/app.html` (Control locked). Shared tokens: `design-concepts/design-system/tokens.css`.

**Role Atlas reference:** `design-concepts/role-atlas.html` is the approved render for the role record (skill map, levels, industries, gap, agent view, method). Match its layout and components; its data is illustrative and never ships as product data. See [Role Atlas surfaces](#role-atlas-surfaces).

## Colors

Palette character: cool mineral neutrals with a restrained cobalt accent and semantic clay/wine states — never hospital teal + warning yellow.

### Primary
- **Present Cobalt** (`#1a4f8c`): Active research, open investigation, present matrix cells, primary accent actions, focus ring. Soft wash `#d6e4f2`, ink `#123a6a`, bright `#3d78bc`.

### Secondary
- **Clay Uncertainty** (`#8a5c48`): Awaiting, uncertainty callouts, retained doubt. Soft `#efe6e0`. Not amber/gold.

### Tertiary
- **Wine Disagreement** (`#8e2f45`): Disagreement cells, human gate, reject/hold actions. Soft `#f0d9df`.
- **Analysis Violet** (`#5c4a78`): Provenance / analysis grouping only. Soft `#ebe6f2`. Use sparingly.

### Neutral
- **Ink Navy** (`#0c1828`): Primary text, primary buttons, brand weight. Mid `#163049`.
- **Graphite** (`#3f4a55` / `#5c6772`): Secondary text and labels.
- **Mineral** (`#f2f5f7` / `#e4ebf0`): Canvas and recessed panels.
- **Paper** (`#fafbfc`): Elevated reading surfaces.
- **Rule** (`#163049` / `#bcc8d4`): Structural borders.

### Named Rules
**The Anti–White-Coat Rule.** Never use teal/cyan scrub greens or amber/yellow clinical warning for present or uncertainty. Cobalt and clay only.

**The Semantic Color Rule.** Color encodes evidence state or action consequence. Do not recolor chrome for mood.

**The Rarity Rule.** Present cobalt should remain scarce on any screen — selection, active state, and present evidence — not wallpaper.

## Typography

**Display Font:** STIX Two Text (Times New Roman, Times, serif)  
**Body Font:** IBM Plex Sans (Helvetica Neue, Helvetica, Arial, sans-serif)  
**Label/Mono Font:** IBM Plex Mono (Courier New, Courier, monospace)

**Character:** Editorial research serif for claims and headlines; industrial sans for UI chrome; mono reserved for run IDs, timestamps, matrix marks, and section instruments.

### Hierarchy
- **Display** (600, `clamp(1.85rem, 4.2vw, 2.85rem)`, ~1.15): Hero / atlas title.
- **Headline** (600, `clamp(1.45rem, 3vw, 1.95rem)`): Investigation title.
- **Title** (600, ~1.15–1.2rem): Brief section and viz titles.
- **Finding** (500, ~1.05rem, serif): Primary claim sentences.
- **Body** (400, 0.9375rem / 1.55): Supporting prose; measure ~48–65ch.
- **Label** (500, 0.6875rem, mono, uppercase, ~0.08em tracking): Instruments, legends, section numbers, callout labels.

### Named Rules
**The Mono Discipline Rule.** Monospace is for measurement and instruments only — never as a costume for “technical vibe” on body copy.

## Layout

Max content width **1120px**. Provenance rail **12rem** sticky beside the brief on desktop; collapses to horizontal scroller under ~860px. Main padding `0 1.25rem` with generous bottom clearance. Spacing scale: `xs 0.35rem` · `sm 0.6rem` · `md 1rem` · `lg 1.5rem` · `xl 2.25rem` · `2xl 3.5rem`. Related claim/matrix cells stay tight; major sections (roles → investigation → approval) get `lg`–`xl` separation. Background: fixed mineral wash (soft present/navy radials over paper→mineral gradient) — cohesive atlas atmosphere, not decorative grids.

## Elevation & Depth

Flat by default. Depth comes from tonal layering (paper on mineral, soft present washes on active cells) and 1–1.5px borders. Soft motion shadows on viz hover only; no hard offset / neobrutal shadows; no glassmorphism.

### Named Rules
**The Flat Desk Rule.** Surfaces rest flat. Do not invent card stacks with drop shadows to fake hierarchy.

## Shapes

Square language: **0px radius** on buttons, panels, callouts, matrix cells, and rail items. Borders are full-perimeter or a **≤2px top hairline** accent for state. Geometry for presence/absence marks is crisp (filled square, slash pattern) — authored SVG/CSS geometry, not illustration scenery.

### Named Rules
**The No Side-Tab Rule.** Never use a thick colored `border-left` / `border-right` (>1px) on cards, callouts, claim lists, or alerts. Use top hairline, full tinted border, or background wash.

## Components

### Buttons
- **Shape:** Square (0). Min height 44px. Padding `0.55rem 0.9rem`. Border 1.5px.
- **Primary:** Navy fill, white text → hover navy-mid.
- **Present:** Cobalt fill → hover present-ink (continue / open research).
- **Present-soft:** Present-soft fill, present-ink text (secondary research action).
- **Secondary:** White fill, navy border/text.
- **Disagree:** Wine fill (gate reject / disagreement action).
- **Ghost:** Transparent, rule-soft border.
- **Focus:** 2px present outline, 2px offset.

### Callouts
Uncertainty / disagreement / provenance: tinted wash + matching **top 2px** hairline + full soft border. Uppercase mono label. No left rail.

### Claim lists
Paper/white cells with present-soft wash and **top 2px present** accent (not left bar). Meta line in mono violet/graphite.

### Coverage matrix
Present = cobalt wash + mark; Absent = slash pattern on white; Disagree = wine wash. Hover may scale/highlight (~200ms, `cubic-bezier(0.16, 1, 0.3, 1)`). Encode state with mark + label, not color alone.

### Provenance stages / bars
Stage chips and bar charts use semantic fills; inset bottom accent allowed. Counts of present lenses are honest; never invent % coverage.

### Instrument rail
Sticky observatory nav: full-cell selection with present-soft fill and present-ink text — not a thick left border. Numbers in mono.

### Status swatches
Small squares: fill = present/uncertain/disagree; slash = idle/absent. Always paired with text.

### Motion
Ease `cubic-bezier(0.16, 1, 0.3, 1)`. Feedback 120–200ms; viz hover ≤250ms. Honor `prefers-reduced-motion` (keep color/opacity state; drop spatial motion).

## Role Atlas surfaces

The role page is one reading column (max 1120px) that stacks the masthead, then the role bar, then the tabs, then Method, then Decisions. Reference: `design-concepts/role-atlas.html`. Anything new on these surfaces follows the patterns below rather than inventing new ones.

### Structure
- **Role bar:** a segmented role picker, then the role head. The role head is a paper panel with the title, status chip, O*NET anchor line, core tasks, and BLS foundation stats. Each source in the head carries a timing tag.
- **Tabs:** Skill map (default) · Levels · Industries · Your gap · For agents. Tabs are text with a 3px cobalt bottom bar when selected over a 1px rule. They use `role="tablist"`, arrow keys move between them, and the selected tab persists per viewer.
- **Method** sits below the tabs: three numbered step cards, the source-timing table, the status-rules table, and the track record. The layer model and the posting-triage example live in collapsed `<details>` panels.

### Status and timing vocabulary
- **Skill status:** `core` (ink outline) · `rising` (cobalt wash) · `early signal` (clay wash) · `fading` (graphite, dashed). `disputed` is a wine text flag, not a status. These four are the only status names.
- **Source timing tags:** `leading` (clay) · `coincident` (cobalt) · `lagging` (graphite, dashed) · `long-term` (violet). Every signal shown to a reader carries one. O*NET ratings and ESCO links are always `lagging`, and BLS projections are always `long-term`.
- **Experimental:** emerging roles show an `experimental` chip in the head, a clay caveat paragraph, and a small mono `experimental` tag in the role picker.

### Skill map
- Rows are grouped into status lanes in the order core, rising, early signal, fading. Each lane has a recess header holding its chip, a one-line meaning, and a count.
- A row is a full-width button with four columns: skill · level pips · evidence squares · forecast. Below 640px it becomes two columns, and the forecast wraps onto its own line.
- **Level pips:** one numbered square per level, filled ink-mid where 15%+ of that level's postings ask for the skill.
- **Evidence squares:** `P` (postings), `O` (official map: O*NET or ESCO) and `M` (market trend), filled cobalt when that evidence family is present. Every row has an `aria-label` that spells out its pips and squares.
- **Forecast cell:** mono, with the percentage in ink, for example "72% · 20%+ of L1–L3 postings by 2027-Q3". Core rows show an en dash.
- **Detail panel** (sticky right column ≥940px, below the map otherwise): status chip, a plain-language reason, the evidence list (source name + timing tag + reading), the forecast box (cobalt top hairline on present-soft, with a large serif percentage, the base rate and how the claim resolves), share-by-level bars, and "asked together with".
- **"What the top level adds"** is a violet analysis strip (analysis-soft with a 1px violet top rule) above the lanes.
- **Connections** is an alternate view: inline SVG, columns = level first asked, family bands, dots sized by share and colored by status, lines only above the published threshold. It is never the default view.

### Industries
- A sector picker (broad NAICS sectors), a sector sheet (NAICS line, the largest sub-industries, three foundation stats, a "How it differs here" cobalt step box, duties, a certifications table with `required` / `preferred` / `common` tags, and domain knowledge as recess tags), and a heatmap of the same skills across sectors.
- **Heatmap cells:** `color-mix(in srgb, var(--present) N%, var(--surface))` with the value printed in the cell. The selected sector's column is outlined in cobalt. A sector below the evidence floor shows a clay caveat in place of a profile and en dashes in the heatmap. It is never hidden.

### Numbers and honesty
- Each probability is shown as a percentage, with the base rate it started from beside it.
- Calibration is a bar per probability bin with a tick at the stated midpoint, plus n.
- Any page or section with non-research data carries the clay "Illustrative data" or "Fixture data" callout. Never present fixture numbers as findings.

### Dark mode
Every role-page token is redefined under `@media (prefers-color-scheme: dark)` (guarded by `:root:not([data-theme="light"])`) and again under `:root[data-theme="dark"]`. Use the dark values from the reference: canvas `#0e1620`, surface `#17232f`, present `#86b2e6`, uncertain `#d5a690`, disagree `#e48ea4`, analysis `#b9aade`. Selected controls invert to light fill and dark text.

### Acceptance for any role surface
- No horizontal page scroll at 390px. Wide tables and the SVG scroll inside their own container.
- 44px minimum hit targets, visible focus ring, and keyboard operation for tabs, pips rows, and segmented controls.
- Zero console errors. Light and dark mode both render from tokens, with no hard-coded hex outside token blocks.

## Do's and Don'ts

### Do
- Follow tokens in `design-concepts/design-system/tokens.css` and this file for every new UI surface.
- Lead with evidence visualizations humans can scan (matrix, bars, stages, maps).
- Keep private/local/approval language explicit.
- Theme `::selection` and focus from present cobalt.
- Archive explorations under `design-concepts/archive/` — do not ship them as product UI.

### Don't
- Don't reintroduce teal/cyan + amber/yellow clinical pairs.
- Don't use side-tab accent borders on content blocks.
- Don't add Inter / Roboto / Plus Jakarta / purple-glow SaaS chrome.
- Don't round the Control system into a soft consumer app (Signal was rejected).
- Don't invent coverage percentages or individual career predictions.
- Don't treat Pixel Ops, Signal, or Editorial Brutalism as active direction.
