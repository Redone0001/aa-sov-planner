"use strict";
(() => {
    const board = document.getElementById("sov-board");
    const editor = document.getElementById("sov-editor");
    const content = document.getElementById("sov-editor-content");
    const modal = editor && window.bootstrap ? new bootstrap.Modal(editor) : null;
    let busy = false;
    let previewRequest = null;
    let previewSequence = 0;
    let opener = null;
    const headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"};

    function filterRows() {
        const query = (document.getElementById("system-filter")?.value || "").trim().toLocaleLowerCase();
        const rows = [...document.querySelectorAll(".sov-system-row")];
        for (const row of rows) row.hidden = !row.textContent.toLocaleLowerCase().includes(query);
        const empty = document.getElementById("filter-empty");
        if (empty) empty.hidden = !rows.length || rows.some(row => !row.hidden);
    }
    function feedback(id, message, danger = false) {
        const target = document.getElementById(id);
        if (!target) return;
        target.className = `alert ${danger ? "alert-danger" : "alert-success"}`;
        target.textContent = message;
        target.hidden = false;
    }
    async function jsonRequest(url, options = {}) {
        const response = await fetch(url, {credentials: "same-origin", ...options, headers});
        if (response.redirected || !response.headers.get("content-type")?.includes("application/json")) {
            throw new Error("Your session may have expired. Refresh the page and sign in before continuing.");
        }
        if (!response.ok) throw new Error(`Request failed (${response.status}). Check your access and refresh the plan before retrying.`);
        return response.json();
    }
    function updateBoard(html) {
        const scrollAreas = [...document.querySelectorAll(".nav-padding.overflow-auto, .table-responsive")];
        const positions = scrollAreas.map(el => [el, el.scrollTop, el.scrollLeft]);
        const tableLeft = board.querySelector(".table-responsive")?.scrollLeft || 0;
        board.innerHTML = html;
        filterRows();
        for (const [el, top, left] of positions) if (el.isConnected) { el.scrollTop = top; el.scrollLeft = left; }
        const table = board.querySelector(".table-responsive");
        if (table) table.scrollLeft = tableLeft;
    }
    async function preview(form) {
        if (!form.dataset.routePreview) return;
        previewRequest?.abort();
        const sequence = ++previewSequence;
        const result = form.querySelector("[data-route-result]");
        const source = form.elements.source.value;
        const destination = form.elements.destination.value;
        result.className = "alert alert-secondary";
        if (!source || !destination) { result.textContent = "Choose source and destination to check the stargate path."; return; }
        result.textContent = "Checking stargate path…";
        previewRequest = new AbortController();
        try {
            const url = new URL(form.dataset.routePreview, window.location.origin);
            url.searchParams.set("source", source); url.searchParams.set("destination", destination);
            const data = await jsonRequest(url, {signal: previewRequest.signal});
            if (sequence !== previewSequence || !result.isConnected) return;
            result.className = `alert ${data.valid ? "alert-success" : "alert-danger"}`;
            result.textContent = data.message;
            if (data.path) {
                const path = document.createElement("div"); path.className = "fw-bold mt-2";
                path.textContent = data.path.map(node => `${node.name} (${node.mode})`).join(" → ");
                result.append(path);
            }
        } catch (error) {
            if (error.name === "AbortError" || sequence !== previewSequence) return;
            result.className = "alert alert-warning"; result.textContent = error.message;
        }
    }
    function prepareForm() {
        const form = content.querySelector("form");
        form?.querySelector(".sov-repeat")?.removeAttribute("hidden");
        if (form) preview(form);
    }
    document.getElementById("project-switch")?.addEventListener("change", event => window.location.assign(event.target.value));
    document.getElementById("system-filter")?.addEventListener("input", filterRows);
    document.addEventListener("change", event => {
        const form = event.target.closest("form[data-route-preview]");
        if (form && ["source", "destination"].includes(event.target.name)) preview(form);
    });
    document.addEventListener("click", async event => {
        const cancel = event.target.closest("[data-sov-cancel]");
        if (cancel && modal && editor.contains(cancel)) { event.preventDefault(); if (!busy) modal.hide(); return; }
        const link = event.target.closest("a[data-sov-edit]");
        if (!link || !modal || event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
        event.preventDefault();
        if (busy) return;
        busy = true; opener = {href: link.href, row: link.closest("tr")?.id};
        document.getElementById("sov-editor-feedback").hidden = true;
        document.getElementById("sov-editor-title").textContent = "Loading editor…";
        content.textContent = "Loading…"; modal.show();
        try {
            const data = await jsonRequest(link.href);
            document.getElementById("sov-editor-title").textContent = data.title;
            content.innerHTML = data.html; prepareForm();
            content.querySelector("select, input:not([type=hidden])")?.focus();
        } catch (error) { feedback("sov-editor-feedback", error.message, true); content.textContent = ""; }
        finally { busy = false; }
    });
    document.addEventListener("submit", async event => {
        const form = event.target;
        const editing = modal && editor.contains(form) && form.matches("[data-sov-form]");
        const removing = board && form.matches("[data-sov-delete]");
        if (!editing && !removing) return;
        event.preventDefault(); if (busy) return;
        busy = true;
        const button = form.querySelector("[type=submit]"); button.disabled = true;
        const keepOpen = Boolean(form.querySelector("#sov-add-another:checked"));
        const payload = new FormData(form);
        try {
            const data = await jsonRequest(form.action, {method: "POST", body: payload});
            if (!data.saved) {
                content.innerHTML = data.html; prepareForm();
                content.querySelector(".alert-danger, .text-danger")?.scrollIntoView({block: "nearest"});
                return;
            }
            updateBoard(data.board);
            feedback("sov-feedback", data.message);
            if (editing && keepOpen) {
                form.reset();
                form.elements.upgrade.value = "";
                form.querySelectorAll(".alert-danger, .text-danger").forEach(el => el.remove());
                feedback("sov-editor-feedback", data.message);
                form.elements.upgrade?.focus();
            } else if (editing) { busy = false; modal.hide(); }
        } catch (error) {
            feedback(editing ? "sov-editor-feedback" : "sov-feedback", `${error.message} If saving was interrupted, refresh the plan to confirm its state before retrying.`, true);
        } finally { busy = false; button.disabled = false; }
    });
    editor?.addEventListener("hide.bs.modal", event => { if (busy) event.preventDefault(); });
    editor?.addEventListener("hidden.bs.modal", () => {
        previewRequest?.abort(); previewSequence++;
        const link = [...board.querySelectorAll("a[data-sov-edit]")].find(a => a.href === opener?.href);
        link?.focus({preventScroll: true});
    });
    document.querySelectorAll("form[data-route-preview]").forEach(preview);
})();
