Build a single-file marketing landing page at `index.html` in the current directory for a
fictional product: **Bloomly** — a plant-care reminder app for apartment dwellers.

Hard requirements:
1. ONE self-contained file. No build step, no npm, no external JavaScript libraries or CSS
   frameworks. Google Fonts via <link> is allowed; nothing else external.
2. Sections in this order: sticky header with nav, hero (headline + subhead + email capture
   form + one CTA button), a 3-item feature grid, a 3-tier pricing table where the middle tier
   is visually marked as recommended, an FAQ with at least 4 questions that expand/collapse,
   and a footer.
3. Responsive: must work at 375px width with no horizontal scroll, and at desktop width.
4. Light and dark theme, driven by CSS custom properties, respecting prefers-color-scheme,
   plus a working manual toggle button in the header that overrides it.
5. The email form must validate a non-empty, well-formed address in vanilla JS and show an
   inline success or error message. It must not navigate away or reload the page.
6. Accessibility: one <h1> only, every form control labelled, the FAQ toggles reachable by
   keyboard, visible :focus styles, and colour contrast at least 4.5:1 for body text.
7. Write your own copy. Do not use lorem ipsum.

When finished, print a short summary of the structure you built and any tradeoff you made.
Do not create any file other than index.html.
