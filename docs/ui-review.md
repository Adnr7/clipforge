# ClipForge UI review

Use this process for UI changes after the code is implemented. Automated tests
can confirm structure and interactions, but they do not replace a screenshot
review. Capture fresh screenshots from the production build or the same stable
browser setup used by the change; do not rely on an old screenshot or claim a
visual check that was not performed.

## Viewport matrix

Review Dark, Light and Nord themes at these CSS viewport widths:

| Context | Widths |
| --- | ---: |
| Desktop | 1440px and 1920px |
| Laptop | 1280px and 1366px |
| Mobile | 390px |

Keep the browser zoom at 100% for the matrix, then repeat the key screens at
200% zoom. Use a consistent height suitable for the screen under review and
record the viewport, zoom, theme, browser, and build being inspected. Stable
dimensions make before/after screenshots comparable.

## Content and state coverage

Use realistic content rather than only short placeholders:

- long project names, source filenames, candidate hooks, rationales, transcript
  lines, provider names, error text, and download labels;
- dashboard with projects, an empty project library, and project cards;
- workspace with source media, transcript, candidates, selected/rendered clips,
  and the compact workflow strip;
- loading, retry, error, empty, disabled, saving, active-render, and dialog
  states;
- settings in all three themes, including configured-provider summaries and long
  validation or connection errors;
- clip preview and download/export controls, including their completed and
  unavailable states.

## Pixel and interaction checklist

For every screenshot, inspect the whole viewport and then the changed region:

- no clipped text, cards, dialogs, controls, media, or focus indicators;
- no horizontal page overflow at 390px or at 200% zoom;
- no overlap between header actions, numbered workflow badges, status notices,
  buttons, labels, or modal content;
- grids and media keep stable, intentional geometry at each breakpoint;
- text wraps naturally and remains readable with realistic long values;
- body text, muted text, badges, form controls, links, and button labels have
  sufficient color contrast in all three themes;
- interactive targets, especially buttons, links, checkboxes, and dialog close
  actions, have at least a 44px usable height/width where applicable;
- keyboard focus is visible, dialogs stay within the viewport, and disabled or
  loading controls communicate their state without depending on color alone;
- video and image surfaces preserve their intended aspect ratio without
  cropping important controls or introducing unexpected scrollbars.

## Review record

After review, keep a short record in the change description or verification
note:

```text
Build/revision:
Browser:
Themes: dark, light, nord
Viewports: 1440, 1920, 1280, 1366, 390; 200% zoom
States exercised:
Screenshots inspected:
Findings and follow-ups:
```

If a viewport or state was not checked, say so. Passing unit, integration, or
browser tests alone is not evidence that every screenshot geometry, overflow,
contrast, or text case is correct.
