"use strict";
document.getElementById("project-switch")?.addEventListener("change", (event) => {
    window.location.assign(event.target.value);
});
document.getElementById("system-filter")?.addEventListener("input", (event) => {
    const query = event.target.value.trim().toLocaleLowerCase();
    const rows = [...document.querySelectorAll(".sov-system-row")];
    for (const row of rows) row.hidden = !row.textContent.toLocaleLowerCase().includes(query);
    document.getElementById("filter-empty").hidden = !rows.length || rows.some((row) => !row.hidden);
});
