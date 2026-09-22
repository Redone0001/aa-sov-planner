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
