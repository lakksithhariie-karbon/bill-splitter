# Production AP — Spatial Layout Only

Spatial arrangement reference for restructuring the POC review surface.
No field schemas, workflow vocabulary, or product copy.

Observed read-only. Agent D confirms: no mutating clicks (nothing that writes).

---

## 1. ASCII wireframes

Role labels only. Proportions approximate what was on screen (~30/70 split on review).

### App shell (list surfaces)

```
+------------------------------------------------------------------+
| context bar (sticky)                              avatar / menu  |
+--------+---------------------------------------------------------+
|        | page title                         [secondary] [primary]|
|  nav   |---------------------------------------------------------|
| sidebar|  segment tabs                                           |
| (persists;|------------------------------------------------------|
|  collapsible)|  filter / search strip                            |
|        |---------------------------------------------------------|
|        |                                                         |
|        |  queue list  OR  data table                             |
|        |  (main scroll)                                          |
|        |                                                         |
|        |---------------------------------------------------------|
|        |  pagination / density controls (footer strip)           |
+--------+---------------------------------------------------------+
```

### Queue list row geometry

```
+------------------------------------------------------------------+
| [ ]  icon   primary line .........................  overflow menu|
|             secondary line (quiet)                               |
+------------------------------------------------------------------+
  ^card-like row: generous vertical padding, full-width, low chrome
```

### Data table geometry

```
+------------------------------------------------------------------+
| [ ]  | col | icon | col | col | col | amount(end) | ...          |  <- sticky header row
|------+-----+------+-----+-----+-----+-------------+--------------|
| [ ]  |     |      |     |     |     |        ###  | ...          |  <- compact row height
| [ ]  |     |      |     |     |     |        ###  | ...          |
+------------------------------------------------------------------+
  columns: checkbox + identity/meta + trailing overflow
  amounts right-aligned; text columns start-aligned
```

### Review surface (document + form)

```
+------------------------------------------------------------------+
| context bar (sticky, global)                                     |
+------------------------------------------------------------------+
| [back]  context label   position cue    [danger]  [PRIMARY CTA]  | <- sticky local bar
+---------------------------++-------------------------------------+
|                           ||  mode tabs / config                 |
|   document preview        ||-------------------------------------|
|   ~30% width              ||  field form                         |
|                           ||  (2-col grid, airy padding)         |
|   zoom / rotate chrome    ||                                     |
|   pages stack vertically  ||  section blocks                     |
|   (own scroll)            ||                                     |
|                           ||  line table (wide; sticky thead)    |
|         || resize handle  ||  (horizontal overflow if needed)    |
|                           ||                                     |
|                           ||  totals block (end of form scroll)  |
|                           ||  (form column = own scroll, ~70%)   |
+---------------------------++-------------------------------------+
```

### Configuration overlay (spatial only)

```
+------------------------------------------------------------------+
|                         [ modal / dialog ]                       |
|                         centered over review                     |
|                         footer: reset | cancel | confirm         |
+------------------------------------------------------------------+
```

---

## 2. Spatial principles

Portable rules — no production product knowledge required.

1. **Shell persistence** — Global context bar and left nav stay put while the main pane changes. Nav collapses to icons; it does not disappear on review.

2. **One primary CTA, top-right** — On review, the decisive action sits in a sticky local bar above both panes, right-weighted and visually heavier than secondary/danger actions beside it. Do not bury the primary action at the bottom of a long form.

3. **Side-by-side verification** — Document preview and editable form share one horizontal band. Preview is the narrower pane (~¼–⅓); form is the wider pane (~⅔–¾). A drag handle lets the reviewer rebalance width.

4. **Independent scroll regions** — Preview scrolls alone; form scrolls alone. Global chrome and the local action bar do not scroll away. Line-table headers stick within the form scroll.

5. **Airy form density, dense tables** — Header fields use comfortable vertical rhythm and a two-column grid with clear section breathing room. Line tables use tighter row height, sticky headers, and end-aligned numeric cells.

6. **List surfaces: card vs table** — Intake/pending items read as padded full-width cards (low visual noise). Settled/history lists use a compact multi-column table with a sticky header and a footer pagination strip.

7. **Filter strip above lists** — Search and light filters sit in a single horizontal strip between tabs and the list — not inside rows.

8. **Modals for configuration, not for the main review** — Optional layout/config opens as a centered overlay; the main review stays a full-page split, not a sheet-only experience.

9. **Collapse without losing place** — Sidebar icon-collapse and optional form subsections collapse in place; the split panes and sticky CTAs remain.

---

## 3. Restructuring proposal (POC journey)

Keep our journey **upload → extract → review → save**. Keep filmstrip, cut toggles, coverage/duplicate banners, cost panel, and multi-doc packets. Reshape **space**, not product scope.

Allowed primitives: `sidebar`, `table`, `card`, `sheet`, `scroll-area`, `badge`, `alert`, `separator`, `tooltip`, `dropdown-menu`, `button`, `input`, `label`, `avatar`, `skeleton`.
Existing feature components stay: `Filmstrip`, `DocumentNavigator`, `PagePreview`, `PageDataPanel`, `CostPanel`, `CoverageBanner`, `ReplayBanner`, `EmptyState`.

### Target spatial shell

```
+------------------------------------------------------------------+
| context bar: brand crumbs | Cost toggle | avatar                 |
+--------+---------------------------------------------------------+
| sidebar|  journey buttons: Upload | Extract | Save               |
|  logo  |  status line (alert if error)                           |
|  Extraction|-----------------------------------------------------|
|  DocumentNavigator|  [CoverageBanner / ReplayBanner]             |
|  (multi-doc)|----------------------------------------------------|
|  avatar|                                                         |
|        |  REVIEW SPLIT (after extract)                           |
|        |  +--------------------++------------------------------+ |
|        |  | document preview   || field form + line table      | |
|        |  | PagePreview        || PageDataPanel                | |
|        |  | ~35% scroll-area   || ~65% scroll-area             | |
|        |  +--------------------++------------------------------+ |
|        |                                                         |
|        |  PRE-EXTRACT (upload phase)                             |
|        |  Filmstrip card (full width, cuts)                      |
|        |  then same split: preview | empty/skeleton form         |
|        |                                                         |
|        |  CostPanel: collapsible right rail OR sheet             |
+--------+---------------------------------------------------------+
```

### Concrete rearrangements

| Region | Current POC tendency | Proposed spatial change |
|--------|----------------------|-------------------------|
| Journey actions | Toolbar mixed with content | Keep Upload / Extract / Save in a sticky strip under the context bar; **Save** is the heavy primary on the right (mirror production CTA weight without renaming our journey). |
| Document ↔ form | Stacked cards (`lg:grid-cols-2`) below filmstrip | After extract, promote to a **persistent horizontal split**: `PagePreview` left (~⅓), `PageDataPanel` right (~⅔), each in its own `scroll-area`. Filmstrip stays **above** the split as packet/cut control — our feature, not removed. |
| Multi-doc packet | `DocumentNavigator` in sidebar | Keep in `sidebar`; treat it as the queue list spatially (select doc → form/preview update). Optional `badge` on items for attention density only. |
| Line items | Inside `PageDataPanel` | Keep; ensure `table` header is sticky inside the form `scroll-area`; numeric columns end-aligned; allow horizontal scroll inside the panel rather than shrinking the preview. |
| Cost | Toggle rail | Keep `CostPanel`; dock as collapsible right column or `sheet` so it never steals the primary split’s vertical rhythm. |
| Banners | Top of inset | Keep `CoverageBanner` / `ReplayBanner` as full-width `alert` bands **above** the split, not overlaid on the preview. |
| Empty / loading | `EmptyState` / spinner | Keep; use `skeleton` blocks in the form pane while extracting so the split geometry appears before data arrives. |
| Mobile / narrow | Stack | Below `lg`, stack preview then form (preview first); keep sticky Save bar. |

### What not to invent

- No new layout library. If a resize handle is desired later, justify a small primitive; until then fixed ~35/65 CSS split is enough.
- Do not replace filmstrip, cuts, or multi-doc navigator with a production-like single-file card list.
- Do not move Save to a bottom-only footer; keep a top sticky primary.

### Minimal component map

- `sidebar` + `avatar` — shell + DocumentNavigator  
- `button` — Upload / Extract / Save (+ filmstrip cut controls)  
- `card` — filmstrip container; optional cost card  
- `scroll-area` — preview pane, form pane  
- `table` + `input` + `label` — PageDataPanel  
- `badge` / `tooltip` — attention markers (existing ungrounded/duplicate cues)  
- `alert` — coverage / replay / errors  
- `sheet` — optional CostPanel on small viewports  
- `dropdown-menu` — export actions if crowded in the sticky bar  
- `separator` — between filmstrip and split; between form sections  
- `skeleton` — extracting state inside form pane  

---

*End of spatial layout note.*
