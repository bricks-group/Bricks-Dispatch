# Bricks Design System

> One language across three surfaces — a broker's desk, an agent's pocket, a photographer's field kit.
> Warm paper, ink, and a single terracotta lifted from the mark.

---

## Principles

- **Settled, honest.** Weight carries hierarchy. The accent is earned, never decorative.
- **Data speaks for itself.** No drop shadows, no gradients, no visual noise.
- **2px radius everywhere.** Consistent, tight, precise.
- **Hairlines, not borders.** 1px at 6% ink opacity. The grid is felt, not seen.

---

## Logo

### Mark

The mark is three rectangular bricks arranged to read as a lowercase **b**.

```
[════════════]
[════════════] [═══════════]
```

- **Left column** — 2 bricks stacked vertically → the stem of the `b`
- **Bottom right** — 1 brick → the bowl of the `b`
- All gaps are **uniform 2px** (vertical row gap = horizontal brick gap)
- Brick proportions are **~2:1** (width:height), like actual masonry

**SVG — viewBox `0 0 27 16`:**

```svg
<svg viewBox="0 0 27 16" fill="none">
  <rect x="1"  y="1" width="12" height="6" rx="1.2" fill="#D2542B"/>
  <rect x="1"  y="9" width="12" height="6" rx="1.2" fill="#D2542B"/>
  <rect x="15" y="9" width="11" height="6" rx="1.2" fill="#D2542B"/>
</svg>
```

> The SVG is non-square. Scale with `width = height × (27/16)`.

**Do not** center the top brick — that produces a pyramid, which is wrong.

### Wordmark

- Text: `bricks` (lowercase), General Sans, weight 500
- Letter-spacing: `fontSize × -0.035`
- Mark height: `fontSize × 0.85`, gap: `fontSize × 0.4`
- Color mark: `#D2542B` (terracotta). Mono contexts: match text color.

---

## Color Tokens

### Light mode

| Token | Hex | Role |
|---|---|---|
| `paper` | `#F4F1EB` | Primary background |
| `paperSoft` | `#EDE8DF` | Hover surfaces, active nav |
| `paperDeep` | `#E4DED2` | Nested surfaces, neutral badges |
| `hair` | `#1411100F` | Hairline borders (6% ink) |
| `hairStrong` | `#14111020` | Focus rings, selected states |
| `ink` | `#14110E` | Primary text, primary buttons |
| `inkSoft` | `#3B332A` | Secondary text, body |
| `inkMute` | `#6B6054` | Labels, overlines, icons |
| `inkFaint` | `#A39684` | Timestamps, sub-labels |

### Dark mode

| Token | Hex | Role |
|---|---|---|
| `night` | `#141210` | Primary background |
| `nightSoft` | `#1C1A17` | Elevated surface |
| `nightDeep` | `#23201C` | Nested surfaces |
| `nightHair` | `#F4F1EB14` | Hairline borders |
| `nightHairS` | `#F4F1EB26` | Strong borders |
| `moon` | `#F4F1EB` | Primary text |
| `moonSoft` | `#D6CFC2` | Secondary text |
| `moonMute` | `#8F8678` | Labels, icons |
| `moonFaint` | `#5E574C` | Sub-labels |

### Accent — Terracotta

| Token | Hex | Use |
|---|---|---|
| `brick` | `#D2542B` | Primary CTA buttons, active indicators, charts |
| `brickDeep` | `#A8401F` | Pressed state, dark text on brickSoft |
| `brickSoft` | `#F4D9CC` | Avatar backgrounds, secondary fills |
| `brickTint` | `#FBEFE8` | Badge backgrounds, highlight fills |

### Status

| Token | Hex | Tint | Use |
|---|---|---|---|
| `ok` | `#4A6B3C` | `#E6ECDD` | Delivered, success |
| `warn` | `#B8741F` | `#F6E8D1` | Editing, attention needed |
| `bad` | `#8C2F24` | `#F1DAD4` | Failed, error |

---

## Typography

### Fonts

```
Display / Body  →  General Sans  (400, 500, 600)
Data / Labels   →  JetBrains Mono  (400, 500)
```

**Imports:**
```html
<link href="https://api.fontshare.com/v2/css?f[]=general-sans@400,500,600&display=swap" rel="stylesheet" />
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
```

Never use Outfit, Manrope, or Inter.

### Scale

| Role | Font | Size | Weight | Tracking | Leading |
|---|---|---|---|---|---|
| Page heading | General Sans | 34px | 500 | -1.1px | 1.0 |
| Display | General Sans | 28px | 500 | -0.6px | 1.05 |
| Body | General Sans | 15px | 400 | — | 1.55 |
| Body small | General Sans | 13px | 400 | — | 1.45 |
| KPI value | JetBrains Mono | 36px | 400 | -1px | 1.0 |
| Overline / Label | JetBrains Mono | 10–10.5px | 500 | 0.14–0.18em | — |
| Table header | JetBrains Mono | 10px | 500 | 0.16em | — |
| Data / ID | JetBrains Mono | 12–13px | 400 | — | — |

### Rules

- All numbers — KPIs, prices, times, job IDs — use JetBrains Mono
- Overlines are always uppercase
- Headings use negative tracking; body text does not

---

## Shape & Elevation

| Property | Value |
|---|---|
| Border radius | **2px everywhere** |
| Mobile chips | `border-radius: 999px` (pill — exception) |
| Drop shadows | **None** on any content surface |
| Borders | 1px `hair` token (hairline) |
| Focus ring | 2px solid ink/moon, 2px offset |

---

## Layout

### Global structure

```
┌─────────────────────────────────────────────────┐
│  Sidebar (232px)  │  Topbar (64px, border-bottom)│
│  border-right     ├─────────────────────────────┤
│                   │  Scrollable content          │
│                   │  padding: 0 36px             │
└─────────────────────────────────────────────────┘
```

- Sidebar: `232px` fixed, `1px hair` right border, `paper`/`night` background
- Topbar: `64px` tall, `1px hair` bottom border — **flat, no blur, no glassmorphism**
- Content: `36px` horizontal padding

### Grid Borders pattern

Used for KPI grids, day-summary strips, and any multi-cell data layout.

```jsx
// Container
<div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 1, background: hair, border: `1px solid ${hair}`, borderRadius: 2 }}>
  // Each cell
  <div style={{ background: paper, padding: '24px' }}>
    ...
  </div>
</div>
```

The container's `background` color becomes the visible 1px grid lines. Cells use `background: paper` to punch through. **Never use card gaps or shadows** for data grids.

### Spacing scale

`4 · 6 · 8 · 10 · 12 · 14 · 16 · 18 · 20 · 24 · 28 · 32 · 36 · 48`

Do not introduce arbitrary values outside this scale.

---

## Components

### Sidebar

```
Width:        232px
Background:   paper / night
Border:       1px hair (right)
Padding:      24px 0
```

- **Wordmark** — `BricksWordmark` at `size={17}`
- **Workspace selector** — 1px hair border, 2px radius, `chevronD` icon
- **Nav item** — 14px General Sans, 17px icon, 12px gap, `10px 14px` padding, 2px radius
  - Active: `paperSoft`/`nightSoft` bg, weight 500, `2px solid brick` left inset bar
  - Inactive: `inkMute`/`moonMute` color, weight 400
- **User footer** — 1px hair top border, 30×30 avatar (2px radius, `brickSoft` bg), 12px name, 11px role in `inkMute`

### Topbar

```
Height:    64px
Border:    1px hair (bottom) — NO backdrop-blur
Padding:   0 36px
```

Contains: overline page title · date range (13px `inkSoft`) · search bar (220px, 1px hair border, ⌘K hint) · bell icon

### KPI Grid

4-column Grid Borders container. Per cell:

```
Padding:   24px
Label:     Overline (inkMute/moonMute)
Value:     JetBrains Mono · 36px · weight 400 · tracking -1px
Delta:     Mono 12px · arrowUp/arrowDn icon · ok/bad color
Sub:       11px · inkFaint
```

### Table

```
Outer:       1px hair border, 2px radius
Header row:  Mono 10px · tracking 0.16em · uppercase · inkFaint · padding 10px 24px
Data row:    13px · padding 16px 24px · 1px hair bottom border (not last)
Numbers:     Right-aligned, JetBrains Mono
```

### Buttons

| Variant | Background | Text | Border |
|---|---|---|---|
| Primary (ink) | `ink` | `paper` | none |
| Primary (brick) | `brick` | `#fff` | none |
| Secondary | transparent | `ink` | `1px hairStrong` |

All buttons: `2px` radius, `13–14px` General Sans weight 500, `11–14px 18–22px` padding. With icon: `inline-flex items-center gap-[8px]`.

### Badges

`5px 9px` padding · Mono 10px · `tracking-[0.14em]` · uppercase · `2px` radius · no border

| State | Background | Text |
|---|---|---|
| In progress | `brickTint` | `brickDeep` |
| Scheduled | `paperDeep` / `nightDeep` | `inkSoft` / `moonSoft` |
| Editing | `warnTint` | `warn` |
| Delivered | `okTint` | `ok` |

### Mobile chips (type selector)

`9px 16px` padding · `border-radius: 999px` · Active: `ink` bg `paper` text · Inactive: `paperSoft` bg, `1px hair` border

### Mobile tab bar

`1px hair` top border · `8px 12px 28px` padding · 4-column grid

- Active: `ink` color · `2px brick` top indicator · `strokeWidth 1.75`
- Inactive: `moonFaint` color · `strokeWidth 1.5`
- Label: Mono 10px · `tracking-[0.12em]` · uppercase

### Chart

Line/area chart using brick accent:

```
Stroke:  #D2542B · 1.5px
Fill:    #D2542B · 8% opacity
Grid:    none
Dot:     3px circle at latest point only
```

Period toggles: Mono 11px pills — active has `paperSoft`/`nightSoft` bg + `hairStrong` border.

---

## Icons

Hairline SVG set: `strokeWidth: 1.5` default (`1.75` for active nav items), `strokeLinecap: round`, `strokeLinejoin: round`, `fill: none`.

Use **lucide-react** or **@phosphor-icons/react** (regular/light weight) if not using the bespoke set.

Available icons: `home · calendar · job · camera · user · upload · map · pin · arrow · arrowUp · arrowDn · check · plus · settings · search · bell · dollar · dots · clock · trend · chevronR · chevronD · grid · users · play · nav`

---

## Motion

- `transition-colors duration-200` on all interactive elements
- No dramatic entrance animations on desktop — data loads quietly
- Respect `prefers-reduced-motion`

---

## Accessibility

- Minimum **4.5:1** contrast for body text
- All actionable elements require `data-testid` (e.g. `data-testid="new-job-btn"`)
- Focus rings: `2px solid ink/moon`, `2px offset`

---

## App Surfaces

| App | Surface | Default theme |
|---|---|---|
| **Brokerage** | Desktop web · 1440px | Light (user-togglable) |
| **Booking** | Agent mobile · 390×844 | Light (user-togglable) |
| **Dispatch** | Photographer mobile · 390×844 | **Dark always** — field legibility at 6am, matte not glossy |

---

## Implementation Notes

- All files are `.js` — no TypeScript, no `.tsx`
- Primary background is `#F4F1EB` (paper), not white
- Never use `rounded-lg`, `rounded-full` (except mobile chips), or `shadow-*`
- Never use `backdrop-blur` on any production surface
- Never use placeholder image URLs — only use explicitly provided media URLs
