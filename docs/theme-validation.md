# AA theme verification

Checked on Alliance Auth 5.3.1 and Django 5.2.17 using AA's own theme switcher and unmodified theme stylesheets (Bootswatch 5.3.3).

| Theme | Planner | Upgrade and route forms | Filter | Mobile |
| --- | --- | --- | --- | --- |
| Flatly | Passed | Passed | Passed | Passed |
| Darkly | Passed | Passed | Passed | Passed |
| Materia | Passed | Passed | Passed | Passed |

Verified that each theme's CSS loads, its AA theme marker is active, and the planner inherits theme colors for the page, buttons, status badges and progress bars. Inspected negative-budget warnings and form controls. No JavaScript errors or failed requests occurred during the browser run. Mobile checks use an iPhone 13 user agent: AA collapses its sidebar and the table scrolls horizontally without overflowing the page.

Fixed the form Cancel button: the outline-secondary variant was difficult to see in Darkly and Materia. It now uses the theme's filled secondary button. No separate planner theme or theme-specific color overrides are introduced.

Three automated integration cases additionally render the project, upgrade, workforce mode and route views with each AA theme selected. They verify theme assets, planner CSS and budget warning/progress markup.

These checks cover the three bundled themes above; third-party themes and administrator custom CSS are not verified. This is a functional and visual check, not a comprehensive accessibility audit.

## Screenshots

All screenshots use synthetic demonstration data.

| Theme | Project | Upgrade form | Mobile table |
| --- | --- | --- | --- |
| Flatly | [Preview](planner-flatly.png) | [Form](form-flatly.png) | [Mobile](mobile-flatly.png) |
| Darkly | [Preview](planner-darkly.png) | [Form](form-darkly.png) | [Mobile](mobile-darkly.png) |
| Materia | [Preview](planner-materia.png) | [Form](form-materia.png) | [Mobile](mobile-materia.png) |

[Browser check results](theme-checks.json)

## Inline editing (0.1.3)

Browser checks in Flatly, Darkly and Materia cover adding/removing upgrades, repeat-add dialogs, valid and invalid transit previews, and saving routes. All completed with zero document navigations and no JavaScript errors. See [results](inline-checks.json) and route dialogs: [Flatly](route-flatly.png), [Darkly](route-darkly.png), [Materia](route-materia.png).

## Compact constellation planner (0.2.0)

Flatly, Darkly and Materia were checked with the compact header and constellation groups. At 1600×1080, five full system rows fit (including examples with multiple upgrades). Browser checks cover collapse/filter interaction, remembered status within repeated additions and across systems, the Plan Manager removal dialog, ratting preview and application, and mobile page overflow. No document navigations or JavaScript errors occurred during in-page edits.

[Browser results](expansion-checks.json)

| Theme | Compact planner | Ratting preview |
| --- | --- | --- |
| Flatly | [Planner](compact-flatly.png) | [Preview](ratting-flatly.png) |
| Darkly | [Planner](compact-darkly.png) | [Preview](ratting-darkly.png) |
| Materia | [Planner](compact-materia.png) | [Preview](ratting-materia.png) |

All screenshots use synthetic systems, alliances and upgrade values. Visible row count depends on viewport size, warnings and upgrade count.

## CSV imports and installation check marks (0.3.0)

Browser checks passed in Flatly, Darkly and Materia for file upload, rejected rows, minimal template download, semicolon CSV with ignored workforce/power/status columns, preview/apply, and marking a Planned upgrade installed. The filter was preserved, and all edits completed with zero document navigations or JavaScript errors. [Results](import-checks.json).

| Theme | CSV preview | Installed action |
| --- | --- | --- |
| Flatly | [Preview](csv-flatly.png) | [Planner](installed-flatly.png) |
| Darkly | [Preview](csv-darkly.png) | [Planner](installed-darkly.png) |
| Materia | [Preview](csv-materia.png) | [Planner](installed-materia.png) |

## Interactive map (0.4.0)

Flatly, Darkly and Materia checks cover three adaptive blue distance zones, workforce paths, 5 LY range highlighting, candidates outside the plan, inline upgrade installation/editing, saved capital selection, pan/zoom and clearing selection. The map remains open during edits with no document navigations or JavaScript errors. The mobile view has no horizontal page overflow. Tests use synthetic coordinates and names, not player planning data.

[Browser results](map-checks.json) · [Flatly](map-flatly.png) · [Darkly](map-darkly.png) · [Materia](map-materia.png)

### Map presentation (0.4.1)

Browser checks in Flatly, Darkly and Materia confirm a system radius of 21 (previously 7), default upgrade icons positioned below systems, a separate vertical label for each upgrade, and unavailable-image tooltips/fallbacks. Synthetic planned/online/offline logistics states verify the presence and online-only range filters across highlight rings, candidate lines and lists; outside-plan systems with unknown upgrades are excluded. Restoring the unfiltered mode restores all range candidates. No JavaScript errors occurred. Image requests were deliberately blocked in the fallback checks; the production URLs use SDE type IDs and EVE's documented Image Server.

### Rectangular systems (0.4.2)

Flatly, Darkly and Materia checks verify rounded rectangular system nodes, centered names fitting inside their measured bounds (including a long synthetic system name), default icons below the nodes, vertical upgrade labels and functioning range filters. Selection and range outlines follow the new shape, and workforce arrows stop at rectangle boundaries. No JavaScript errors occurred.
