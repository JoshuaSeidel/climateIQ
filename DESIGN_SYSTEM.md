# Dark Glassmorphism Design System

A complete reference for reproducing the ClimateIQ dark glassmorphism UI in any React + Tailwind CSS project. This design uses translucent, blurred card surfaces over a dark background in dark mode, with clean solid surfaces in light mode.

---

## Table of Contents

1. [Core Concept](#core-concept)
2. [Tech Stack Requirements](#tech-stack-requirements)
3. [Color Palette](#color-palette)
4. [CSS Custom Properties](#css-custom-properties)
5. [Glassmorphism Tokens](#glassmorphism-tokens)
6. [Typography](#typography)
7. [Component Patterns](#component-patterns)
8. [Layout Patterns](#layout-patterns)
9. [State-Driven Styling](#state-driven-styling)
10. [Tailwind Class Recipes](#tailwind-class-recipes)
11. [Light Mode Strategy](#light-mode-strategy)
12. [Common Mistakes](#common-mistakes)

---

## Core Concept

The design has two distinct modes:

- **Dark mode**: Translucent surfaces with `backdrop-filter: blur()`, colored glow shadows, gradient accents, and ambient background gradients. This is the "hero" mode where glassmorphism shines.
- **Light mode**: Clean, solid white/gray backgrounds with subtle shadows. No blur, no translucency. Glassmorphism does not work on light backgrounds because there is nothing interesting to blur through.

Every component is built with both modes in mind. Dark-specific styles use the `dark:` prefix. Light mode is the default/base.

---

## Tech Stack Requirements

- **Tailwind CSS v4** (CSS-native config via `@theme inline` in your CSS file, no `tailwind.config` file)
- **React** (any version 18+)
- **clsx + tailwind-merge** via a `cn()` utility function
- **class-variance-authority (CVA)** for button variants
- **lucide-react** for icons (any icon library works)

If using Tailwind v3, replace `@theme inline` with a `tailwind.config.ts` file and map the same tokens there.

---

## Color Palette

### Base Colors (Dark Mode)

| Role              | Value                          | CSS Variable        |
|-------------------|--------------------------------|---------------------|
| Main background   | `hsl(222 47% 5%)` / `#0b1120` | `--background`      |
| Card background   | `rgba(10, 12, 16, 0.62)`      | `--glass-bg`        |
| Panel background  | `rgba(2, 6, 23, 0.38)`        | used inline         |
| Chip background   | `rgba(2, 6, 23, 0.30)`        | used inline         |
| Text primary      | `hsl(215 28% 92%)`            | `--foreground`      |
| Text secondary    | `hsl(215 20% 65%)`            | `--muted-foreground` |
| Border default    | `rgba(148, 163, 184, 0.22)`   | `--glass-border`    |
| Border subtle     | `rgba(148, 163, 184, 0.12)`   | used inline         |
| Primary accent    | `hsl(199 89% 60%)` / `#38bdf8` (sky-400) | `--primary` |

### State Colors

| State    | Hex       | Tailwind  | Usage                        |
|----------|-----------|-----------|------------------------------|
| Safe/OK  | `#4ade80` | green-400 | Active, healthy, in-range    |
| Cool     | `#38bdf8` | sky-400   | Primary accent, idle states  |
| Warning  | `#facc15` | yellow-400| Caution, stale data          |
| Danger   | `#ef4444` | red-500   | Errors, out-of-range         |
| Purple   | `#a855f7` | purple-500| Air quality, special metrics |

### Base Colors (Light Mode)

| Role            | Value                    |
|-----------------|--------------------------|
| Background      | `hsl(0 0% 100%)` white   |
| Card            | `hsl(0 0% 100%)` white   |
| Border          | `hsl(214 32% 91%)` light gray |
| Text primary    | `hsl(222 47% 11%)` near-black |
| Text secondary  | `hsl(215 16% 47%)` gray  |
| Primary accent  | `hsl(221 83% 53%)` blue  |

---

## CSS Custom Properties

Paste this into your main CSS file. Adjust colors to your brand.

```css
@import "tailwindcss";

/* ── Light theme ─────────────────────────────────────────────── */
:root {
  --background: 0 0% 100%;
  --foreground: 222 47% 11%;
  --muted: 210 40% 96%;
  --muted-foreground: 215 16% 47%;
  --card: 0 0% 100%;
  --card-foreground: 222 47% 11%;
  --border: 214 32% 91%;
  --input: 214 32% 91%;
  --primary: 221 83% 53%;
  --primary-foreground: 0 0% 100%;
  --secondary: 210 40% 96%;
  --secondary-foreground: 222 47% 11%;
  --destructive: 0 84% 60%;
  --destructive-foreground: 0 0% 100%;
  --ring: 221 83% 53%;

  /* Glassmorphism tokens — light mode uses solid fallbacks */
  --glass-bg: hsl(0 0% 100% / 0.85);
  --glass-border: hsl(214 32% 91%);
  --glass-glow: transparent;
  --glass-blur: 0px;

  /* State colors (HSL without wrapper) */
  --color-safe: 142 71% 45%;
  --color-cool: 199 89% 60%;
  --color-warning: 48 96% 53%;
  --color-danger: 0 84% 60%;
  --color-purple: 271 91% 65%;
}

/* ── Dark theme ──────────────────────────────────────────────── */
.dark {
  --background: 222 47% 5%;
  --foreground: 215 28% 92%;
  --muted: 217 33% 12%;
  --muted-foreground: 215 20% 65%;
  --card: 224 50% 5%;
  --card-foreground: 215 28% 92%;
  --border: 215 25% 27%;
  --input: 215 25% 27%;
  --primary: 199 89% 60%;
  --primary-foreground: 222 47% 11%;
  --secondary: 217 33% 12%;
  --secondary-foreground: 215 28% 92%;
  --destructive: 0 84% 60%;
  --destructive-foreground: 215 28% 92%;
  --ring: 199 95% 74%;

  /* Glassmorphism tokens — dark mode uses translucent values */
  --glass-bg: rgba(10, 12, 16, 0.62);
  --glass-border: rgba(148, 163, 184, 0.22);
  --glass-glow: rgba(56, 189, 248, 0.16);
  --glass-blur: 12px;
}
```

### Tailwind v4 Theme Bindings

Register the CSS variables as Tailwind color tokens so you can use `bg-background`, `text-foreground`, `border-border`, etc.:

```css
@theme inline {
  --font-sans: "Space Grotesk", "SF Pro Display", ui-sans-serif, system-ui;

  --color-background: hsl(var(--background));
  --color-foreground: hsl(var(--foreground));
  --color-muted: hsl(var(--muted));
  --color-muted-foreground: hsl(var(--muted-foreground));
  --color-card: hsl(var(--card));
  --color-card-foreground: hsl(var(--card-foreground));
  --color-border: hsl(var(--border));
  --color-input: hsl(var(--input));
  --color-primary: hsl(var(--primary));
  --color-primary-foreground: hsl(var(--primary-foreground));
  --color-secondary: hsl(var(--secondary));
  --color-secondary-foreground: hsl(var(--secondary-foreground));
  --color-destructive: hsl(var(--destructive));
  --color-destructive-foreground: hsl(var(--destructive-foreground));
  --color-ring: hsl(var(--ring));

  --color-safe: hsl(var(--color-safe));
  --color-cool: hsl(var(--color-cool));
  --color-warning: hsl(var(--color-warning));
  --color-danger: hsl(var(--color-danger));
  --color-purple: hsl(var(--color-purple));
}
```

---

## Glassmorphism Tokens

The four glass tokens control the entire effect. They switch between light and dark mode automatically:

| Token            | Light Mode                  | Dark Mode                          |
|------------------|-----------------------------|------------------------------------|
| `--glass-bg`     | `hsl(0 0% 100% / 0.85)`    | `rgba(10, 12, 16, 0.62)`          |
| `--glass-border` | `hsl(214 32% 91%)`         | `rgba(148, 163, 184, 0.22)`       |
| `--glass-glow`   | `transparent`               | `rgba(56, 189, 248, 0.16)`        |
| `--glass-blur`   | `0px`                       | `12px`                             |

### Utility Classes

```css
.glass-card {
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
}

:root .glass-card {
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}

.dark .glass-card {
  box-shadow: 0 0 15px rgba(148, 163, 184, 0.06);
}

.glow-border-primary {
  box-shadow: 0 0 20px var(--glass-glow);
}

.glow-border-safe {
  box-shadow: 0 0 20px hsl(var(--color-safe) / 0.2);
}

.glow-border-warning {
  box-shadow: 0 0 20px hsl(var(--color-warning) / 0.2);
}

.glow-border-danger {
  box-shadow: 0 0 20px hsl(var(--color-danger) / 0.2);
}
```

---

## Typography

The design uses an "instrument panel" feel with heavy font weights for data readability.

### Font

`Space Grotesk` as primary, with `SF Pro Display` as fallback. Set base `font-weight: 500` on the body.

```css
body {
  font-family: var(--font-sans);
  font-weight: 500;
  font-feature-settings: "ss01", "cv01";
}
```

### Weight Scale

| Element              | Weight      | Size         | Extra                          |
|----------------------|-------------|--------------|--------------------------------|
| Hero stat values     | `font-black` (900) | `text-3xl` to `text-4xl` | `tracking-tight`      |
| Section titles       | `font-black` (900) | `text-xl` to `text-2xl`  | `tracking-tight`      |
| Card titles          | `font-bold` (700)  | `text-lg`                | `tracking-tight`      |
| Stat values          | `font-black` (900) | `text-2xl` to `text-3xl` |                       |
| Chip/badge values    | `font-bold` (700)  | `text-sm`                |                       |
| Body text            | `font-medium` (500)| `text-sm`                |                       |
| Labels (uppercase)   | `font-bold` (700)  | `text-[10px]`            | `uppercase tracking-[0.2em]` |
| Muted/secondary text | `font-medium` (500)| `text-xs` to `text-sm`   |                       |

### Label Pattern

All section/category labels use this exact pattern:

```
text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground
```

This creates a small, wide-tracked, all-caps label that reads like an instrument panel.

---

## Component Patterns

### Card

The foundational surface. Light mode is solid; dark mode is translucent with blur.

```tsx
// Light: solid white, subtle shadow
// Dark: translucent near-black, backdrop-blur, faint glow shadow
<div className={cn(
  'rounded-2xl border border-border/30 bg-card/80 p-4 shadow-sm sm:p-6',
  'backdrop-blur-xl',
  'dark:bg-[rgba(10,12,16,0.62)] dark:border-[rgba(148,163,184,0.18)] dark:shadow-[0_0_15px_rgba(148,163,184,0.06)]',
)}>
  {children}
</div>
```

Key properties:
- `rounded-2xl` (16px border radius)
- `backdrop-blur-xl` (applies in dark mode where `--glass-blur` is 12px)
- Dark bg is `rgba(10,12,16,0.62)` -- 62% opacity near-black
- Dark border is `rgba(148,163,184,0.22)` -- 22% opacity slate
- Dark shadow is a faint 15px glow

### Button

Four variants: default, outline, ghost, secondary.

```
Base: rounded-xl text-sm font-semibold

default (primary):
  Light: bg-primary text-primary-foreground shadow-sm
  Dark:  bg-gradient-to-r from-primary/90 to-primary/70
         shadow-[0_0_15px_rgba(56,189,248,0.25)]
         hover:shadow-[0_0_20px_rgba(56,189,248,0.35)]

outline:
  Light: border border-border bg-transparent
  Dark:  border-[rgba(148,163,184,0.25)] bg-[rgba(2,6,23,0.35)]
         hover:bg-[rgba(2,6,23,0.55)]

ghost:
  Light: bg-transparent hover:bg-foreground/10
  Dark:  hover:bg-white/5
```

The key dark-mode detail: default buttons use a gradient background with a colored glow shadow that intensifies on hover.

### Input

```
Light: border-input bg-transparent
Dark:  bg-[rgba(2,6,23,0.38)] border-[rgba(148,163,184,0.22)]
       focus:ring-primary/40 focus:shadow-[0_0_10px_rgba(56,189,248,0.15)]
```

### Chip / Pill

Used for inline metrics, tags, and filter buttons:

```
rounded-full px-3 py-1.5 text-sm
bg-muted/60 border border-border/40
dark:bg-[rgba(2,6,23,0.30)] dark:border-slate-400/20 dark:backdrop-blur-[10px]
```

### Tab Navigation / Segmented Control

A container with toggle buttons inside:

```
Container:
  rounded-2xl border p-1
  border-border/60 bg-muted/40
  dark:border-[rgba(148,163,184,0.15)] dark:bg-[rgba(2,6,23,0.35)]

Active tab:
  bg-primary text-primary-foreground shadow-sm
  dark:bg-gradient-to-r dark:from-primary/80 dark:to-primary/50
  dark:border dark:border-primary/40
  dark:shadow-[0_0_14px_rgba(56,189,248,0.2)]

Inactive tab:
  text-muted-foreground
  dark:hover:bg-white/5
```

### Status Indicator Dot

A small circle that communicates state:

```tsx
<span className={cn(
  'h-3 w-3 rounded-full',
  isActive && 'bg-[#4ade80] animate-pulse',  // green + pulse
  isIdle && 'bg-[#38bdf8]',                   // sky blue
  isUnknown && 'bg-[rgba(148,163,184,0.22)]', // muted
)} />
```

The `animate-pulse` on active/danger states draws attention.

### Stat Card

```tsx
<Card>
  <CardContent className="flex items-center justify-between p-4">
    <div>
      <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
        Label
      </p>
      <p className="text-3xl font-black text-foreground">
        Value
      </p>
    </div>
    <div className={cn(
      'flex h-12 w-12 items-center justify-center rounded-full',
      'bg-orange-500/10',
      'dark:bg-orange-500/15 dark:shadow-[0_0_12px_rgba(249,115,22,0.15)]',
    )}>
      <Icon className="h-6 w-6 text-orange-500" />
    </div>
  </CardContent>
</Card>
```

The icon circle gets a colored glow shadow in dark mode.

### Tooltip (for charts)

```tsx
<Tooltip
  contentStyle={{
    backgroundColor: 'var(--glass-bg, hsl(var(--card)))',
    border: '1px solid var(--glass-border, hsl(var(--border)))',
    borderRadius: '12px',
    backdropFilter: 'blur(12px)',
  }}
/>
```

---

## Layout Patterns

### Root Layout

```tsx
<div className="relative flex h-screen bg-background">
  {/* Ambient gradient overlays -- dark mode only */}
  <div className="pointer-events-none absolute inset-0 overflow-hidden dark:block hidden">
    <div className="absolute -top-32 -left-32 h-[500px] w-[500px] rounded-full
      bg-[radial-gradient(circle,rgba(56,189,248,0.08)_0%,transparent_70%)]" />
    <div className="absolute -top-24 -right-24 h-[400px] w-[400px] rounded-full
      bg-[radial-gradient(circle,rgba(250,204,21,0.05)_0%,transparent_70%)]" />
  </div>

  <Sidebar />
  <main className="relative flex flex-1 flex-col overflow-hidden">
    <Header />
    <div className="flex-1 overflow-y-auto p-3 sm:p-6">
      {children}
    </div>
  </main>
</div>
```

The two radial gradients create a subtle ambient glow behind all content. Sky-blue in the top-left, warm yellow in the top-right. They are `pointer-events-none` and only visible in dark mode.

### Sidebar

```
Container:
  border-r border-border/40 bg-card
  dark:border-[rgba(148,163,184,0.12)] dark:bg-[rgba(10,12,16,0.78)] dark:backdrop-blur-xl

Active nav link:
  bg-primary text-primary-foreground shadow-sm
  dark:bg-gradient-to-r dark:from-primary/80 dark:to-primary/50
  dark:border dark:border-primary/40
  dark:shadow-[0_0_18px_rgba(56,189,248,0.2)]

Inactive nav link:
  text-muted-foreground hover:text-foreground hover:bg-muted/60
  dark:hover:bg-white/5
```

### Header

```
border-b border-border/40 bg-background/90 backdrop-blur-sm
dark:border-[rgba(148,163,184,0.12)] dark:bg-[rgba(10,12,16,0.5)] dark:backdrop-blur-xl
```

---

## State-Driven Styling

Cards and elements change their border color and glow shadow based on data state. This creates a visual language where you can scan the UI and immediately understand status.

### 5-Tier Color System

| Tier     | Color     | Border Class                          | Glow Shadow                                          |
|----------|-----------|---------------------------------------|------------------------------------------------------|
| Danger   | red-500   | `dark:border-l-[#ef4444]`             | `dark:shadow-[0_0_20px_rgba(239,68,68,0.12)]`       |
| Warning  | yellow-400| `dark:border-l-[#facc15]`             | `dark:shadow-[0_0_20px_rgba(250,204,21,0.12)]`      |
| OK/Safe  | green-400 | `dark:border-l-[#4ade80]`             | `dark:shadow-[0_0_20px_rgba(74,222,128,0.12)]`      |
| Cool     | sky-400   | `dark:border-l-[#38bdf8]`             | `dark:shadow-[0_0_20px_rgba(56,189,248,0.12)]`      |
| Neutral  | slate     | `dark:border-l-[rgba(148,163,184,0.22)]` | `dark:shadow-[0_0_20px_rgba(148,163,184,0.06)]`  |

### Example: Status-Driven Card

```tsx
<Card className={cn(
  'group relative overflow-hidden',
  'bg-card shadow-sm border-border/70',
  // Dark mode: colored left border + matching glow
  isActive && 'dark:border-l-2 dark:border-l-[#4ade80] dark:shadow-[0_0_20px_rgba(74,222,128,0.12)]',
  isIdle && 'dark:border-l-2 dark:border-l-[#38bdf8] dark:shadow-[0_0_20px_rgba(56,189,248,0.12)]',
  isError && 'dark:border-l-2 dark:border-l-[#ef4444] dark:shadow-[0_0_20px_rgba(239,68,68,0.12)]',
)}>
```

The `border-l-2` creates a thin colored accent on the left edge. The matching `shadow` creates a soft glow around the entire card.

---

## Tailwind Class Recipes

### Quick Reference: Common Dark-Mode Overrides

```
Card surface:
  dark:bg-[rgba(10,12,16,0.62)] dark:border-[rgba(148,163,184,0.18)]

Panel/section surface:
  dark:bg-[rgba(2,6,23,0.38)] dark:border-[rgba(148,163,184,0.22)]

Chip/pill surface:
  dark:bg-[rgba(2,6,23,0.30)] dark:border-slate-400/20 dark:backdrop-blur-[10px]

Input field:
  dark:bg-[rgba(2,6,23,0.38)] dark:border-[rgba(148,163,184,0.22)]

Sidebar/header surface:
  dark:bg-[rgba(10,12,16,0.78)] dark:backdrop-blur-xl

Subtle border:
  dark:border-[rgba(148,163,184,0.12)]

Standard border:
  dark:border-[rgba(148,163,184,0.18)]

Hover state:
  dark:hover:bg-white/5

Active/selected state:
  dark:bg-primary/15 dark:border-primary/30

Primary glow shadow:
  dark:shadow-[0_0_15px_rgba(56,189,248,0.25)]

Icon glow (drop-shadow):
  dark:drop-shadow-[0_0_6px_rgba(56,189,248,0.4)]
```

### Opacity Levels (Memorize These)

| Opacity | Usage                                    |
|---------|------------------------------------------|
| 0.05    | Ambient background gradients             |
| 0.06    | Faint card glow shadows                  |
| 0.08    | Ambient gradient primary color           |
| 0.10-0.15 | Icon circle backgrounds, active badges |
| 0.12    | Subtle borders, state glow shadows       |
| 0.15-0.20 | Standard glow shadows                  |
| 0.22    | Standard glass borders                   |
| 0.25    | Stronger borders, button glow            |
| 0.30    | Chip backgrounds                         |
| 0.35    | Button/panel backgrounds, hover states   |
| 0.38    | Input/panel backgrounds                  |
| 0.50    | Header backgrounds                       |
| 0.62    | Card backgrounds                         |
| 0.78    | Sidebar background                       |

The pattern: more important/prominent surfaces get higher opacity. Backgrounds that need to feel "solid" (sidebar) are ~78%. Floating elements (chips) are ~30%.

---

## Light Mode Strategy

Light mode is intentionally NOT glassmorphism. Translucent blur effects look bad on white/light backgrounds because there is nothing interesting to see through the blur.

Instead, light mode uses:
- Solid `bg-card` (white) backgrounds
- Subtle `shadow-sm` for depth
- Standard `border-border/30` borders (very light gray)
- No `backdrop-filter`
- No glow shadows (the `--glass-glow` token is `transparent`)

The CSS custom properties handle this automatically. The `--glass-bg` in light mode is `hsl(0 0% 100% / 0.85)` (nearly opaque white), and `--glass-blur` is `0px`.

All dark-mode-specific styles use the `dark:` prefix, so they simply do not apply in light mode.

---

## Common Mistakes

1. **Using glassmorphism in light mode.** It looks muddy and broken. Use solid surfaces instead.

2. **Too much blur.** Keep `backdrop-filter: blur()` between 10-14px. More than that is expensive and looks over-processed.

3. **Forgetting `-webkit-backdrop-filter`.** Safari requires the prefixed version. Always include both.

4. **Glow shadows that are too bright.** Keep glow opacity between 0.12-0.25. Higher values look like neon signs.

5. **Using `bg-opacity` instead of `rgba()`.** For glass surfaces, use explicit `rgba()` values in arbitrary Tailwind classes like `dark:bg-[rgba(10,12,16,0.62)]`. This is more precise than `bg-card/62`.

6. **Forgetting `pointer-events-none` on ambient gradients.** The decorative gradient overlays must not intercept clicks.

7. **Not using `font-black` for stat values.** The instrument-panel feel comes from the extreme weight contrast between 900-weight numbers and 500-weight body text.

8. **Inconsistent border opacity.** Pick 2-3 border opacity levels and stick to them. We use 0.12 (subtle), 0.18 (standard), and 0.22 (prominent).

9. **Skipping the colored left border on status cards.** The `border-l-2` with a state color is what makes cards scannable at a glance.

10. **Using `text-semibold` for important numbers.** Always use `font-black` (900) or `font-bold` (700) for data values. `font-semibold` (600) is too light for the instrument-panel aesthetic.

---

## Applying to a New Project

### Step 1: Set Up the Foundation

1. Copy the CSS custom properties block (`:root` and `.dark`) into your main CSS file.
2. Add the `@theme inline` block for Tailwind v4 (or equivalent `tailwind.config` for v3).
3. Add the utility classes (`.glass-card`, `.glow-border-*`).
4. Add the body styles (font, weight, feature settings).
5. Install Space Grotesk font (or substitute your preferred geometric sans-serif).

### Step 2: Build Base Components

Create your Card, Button, and Input components using the patterns above. These three components carry 80% of the design.

### Step 3: Add the Layout Shell

1. Root layout with ambient gradient overlays.
2. Sidebar with glass background and glowing active nav.
3. Header with glass background and segmented controls.

### Step 4: Style Pages

For each page, apply these rules:
- Section labels: `text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground`
- Section titles: `text-2xl font-black tracking-tight`
- Stat values: `text-2xl font-black` or `text-3xl font-black`
- Cards: use your Card component without extra border/bg overrides
- List items: add `dark:bg-[rgba(2,6,23,0.35)] dark:border-[rgba(148,163,184,0.15)]`
- Tabs/toggles: use the segmented control pattern
- Tooltips/popovers: use glass background with blur

### Step 5: Add State-Driven Styling

Map your data states to the 5-tier color system. Apply colored left borders and matching glow shadows to cards that represent stateful entities.
