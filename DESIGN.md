# Design direction

The committed direction for this build. It is executed in `hopbalecount.py`
(constants `ACCENT_HEX`, `ACCENT_RGB`, `ACCENT_ANSI`, `UNICODE_GLYPHS`,
`ASCII_GLYPHS` and `render_table`) - there is no CSS, so this file is the source
of truth for the terminal surface.

## Archetype and style family

* Archetype: **cli** - the product is a terminal program, not a page.
* Style family: **calm-precise**. Monospace, no chrome, aligned columns,
  box-drawing separators, one accent reserved for primary status.
* Density: **compact** - one header rule, no blank line between rows, two spaces
  between columns.
* Surface: **terminal dark** - the terminal's own background. HopBaleCount never
  emits background colours, so it reads correctly on a dark terminal and stays
  legible when piped.

## Tokens

| Token | Value | Used for |
| --- | --- | --- |
| Accent | `oklch(0.58 0.12 191)` / `#00908b` / ANSI `38;2;0;144;139` | table rules only |
| Neutral | the terminal's own foreground/background | all text |
| Type | system monospace (the terminal's font) | everything |
| Rule | `─` (U+2500), ASCII `-` fallback | header and total separators |
| Separator | `·` (U+00B7), ASCII `-` fallback | inline separators |
| Check | `✓` (U+2713), ASCII `ok` fallback | accepted interactive field |

## Rules carried through every surface

1. **Aligned columns.** One renderer (`render_table`) computes the width of every
   column from header + cells and pads each cell to it. Right-align numbers,
   left-align text. A 40-character bine row never breaks the alignment.
2. **Help-first output.** `--help` and a bare invocation print usage plus three
   runnable examples and the exit-code table; every subcommand has its own help.
3. **One accent, never colour-only.** The accent appears on the table rules and
   nothing else. Every status is already a word (`Added`, `GRAND TOTAL`,
   `Passphrase set.`, `Error:`), so no information is lost without colour.
4. **Colour discipline.** The accent is emitted only when stdout is a TTY, never
   when `NO_COLOR` or `TERM=dumb` is set, and never with `--no-color`. Results
   stay pipe-friendly plain text.
5. **Unicode with a fallback.** Box-drawing glyphs are probed against the output
   encoding; a locale that cannot encode them gets ASCII instead.
6. **Units at the cell.** Weights print `%.2f kg`, alpha prints `%.2f%%`; column
   headers stay unit-free so nothing is printed twice.
7. **Two decimals at print time only.** Rounding never reaches the store.
8. **Nothing decorative.** Every line printed either carries data or tells the
   operator what to do next.
