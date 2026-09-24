"use strict";
(() => {
    const panel = document.getElementById("sov-map");
    if (!panel) return;
    const canvas = document.getElementById("sov-map-canvas");
    const sidebar = document.getElementById("sov-map-selection");
    const chooser = document.getElementById("sov-map-system");
    const message = document.getElementById("sov-map-message");
    const svgNS = "http://www.w3.org/2000/svg";
    let data = null, selected = null, candidates = [], rangeError = "", rangeLoading = false;
    let rangeSequence = 0, loadSequence = 0, dirty = true, drag = null, moved = false;
    let origin = [0, 0], unit = 1, projected = false, view = [0, 0, 1000, 650];
    const layers = () => Object.fromEntries([...panel.querySelectorAll("[data-map-layer]")].map(el => [el.dataset.mapLayer, el.checked]));
    const nodeMap = () => new Map([...candidates, ...(data?.nodes || [])].map(n => [n.id, n]));
    const point = n => [(n.position[0] - origin[0]) / unit, -(n.position[1] - origin[1]) / unit];
    const distance = value => value == null ? "Unknown" : `${value.toFixed(3)} LY`;
    const number = value => value.toLocaleString();
    function element(tag, text, className) {
        const el = document.createElement(tag);
        if (text != null) el.textContent = text;
        if (className) el.className = className;
        return el;
    }
    function svg(tag, attrs = {}, parent = canvas) {
        const el = document.createElementNS(svgNS, tag);
        for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
        parent.append(el); return el;
    }
    function editLink(name, url, parent = sidebar) {
        if (!url) return;
        const a = element("a", name, "btn btn-sm btn-secondary");
        a.href = url; a.dataset.sovEdit = ""; parent.append(a);
    }
    async function request(url) {
        const result = await fetch(url, {credentials: "same-origin", headers: {Accept: "application/json"}});
        if (!result.ok || result.redirected || !result.headers.get("content-type")?.includes("application/json")) throw new Error("Unable to load map data. Check your connection and access, then reopen the map to retry.");
        return result.json();
    }
    function setView() { canvas.setAttribute("viewBox", view.join(" ")); }
    function fit(scope = "plan") {
        const ids = new Set([selected, ...candidates.map(n => n.id)]);
        const nodes = [...nodeMap().values()].filter(n => n.position && (scope === "range" ? ids.has(n.id) : n.planned_id));
        if (!nodes.length) return;
        const positions = nodes.map(point), xs = positions.map(p => p[0]), ys = positions.map(p => p[1]);
        const x = Math.min(...xs), y = Math.min(...ys), w = Math.max(80, Math.max(...xs) - x), h = Math.max(80, Math.max(...ys) - y);
        view = [x-60, y-50, w+170, h+100]; setView();
    }
    function zoom(factor) {
        const w = Math.max(15, Math.min(10000, view[2]*factor));
        factor = w/view[2];
        view = [view[0]+view[2]*(1-factor)/2, view[1]+view[3]*(1-factor)/2, w, view[3]*factor]; setView();
    }
    function palette(nodes) {
        const zones = [...new Set(nodes.filter(n => n.zone).map(n => n.zone))].sort();
        return new Map(zones.map((zone, i) => {
            const t = zones.length > 1 ? i/(zones.length-1) : 0;
            const dark = [18, 60, 130], light = [177, 218, 255];
            return [zone, `rgb(${dark.map((v, j) => Math.round(v + (light[j]-v)*t)).join(",")})`];
        }));
    }
    function draw() {
        if (!data) return;
        const focused = document.activeElement?.dataset?.mapNode;
        canvas.replaceChildren();
        const nodes = nodeMap(), visible = [...nodes.values()].filter(n => n.position), colors = palette(visible), enabled = layers();
        const nearby = new Set(candidates.map(n => n.id));
        const legend = document.getElementById("sov-map-legend"); legend.replaceChildren();
        const ranges = ["0–5", ">5–10", ">10–15", ">15–20", ">20"];
        for (const [zone, color] of colors) {
            const label = element("span", `Zone ${zone}: ${ranges[zone-1]} LY`), swatch = element("span", null, "sov-map-swatch");
            label.dataset.zone = zone; swatch.style.backgroundColor = color; label.prepend(swatch); legend.append(label);
        }
        if (!data.capital) legend.append(element("span", "Set a plan capital to display distance zones.", "text-body-secondary"));
        if (data.capital && !colors.size) legend.append(element("span", "No capital-distance data available. Reload geographic coordinates in the SDE.", "text-warning"));
        const defs = svg("defs");
        const marker = svg("marker", {id:"sov-map-arrow", viewBox:"0 0 10 10", refX:9, refY:5, markerWidth:5, markerHeight:5, orient:"auto-start-reverse"}, defs);
        svg("path", {d:"M 0 0 L 10 5 L 0 10 z", fill:"var(--bs-info, #17a2b8)"}, marker);
        function line(a, b, className, parent = canvas) {
            if (!nodes.get(a)?.position || !nodes.get(b)?.position) return null;
            const [x1,y1] = point(nodes.get(a)), [x2,y2] = point(nodes.get(b));
            return svg("line", {x1,y1,x2,y2,class:className,"vector-effect":"non-scaling-stroke"}, parent);
        }
        if (enabled.gates) for (const [a,b] of data.gates) line(a,b,"sov-map-gate");
        for (const candidate of candidates) {
            const edge = line(selected,candidate.id,"sov-map-ansiblex");
            if (edge) svg("title",{},edge).textContent = `Possible connection: ${distance(candidate.source_distance)} (range only)`;
        }
        if (enabled.routes) for (const route of data.routes) {
            const path = route.valid ? route.path : [route.source, route.destination];
            const group = svg("g");
            const title = svg("title", {}, group); title.textContent = `${nodes.get(route.source)?.name} → ${nodes.get(route.destination)?.name}: ${number(route.amount)} workforce${route.valid ? "" : " (invalid route)"}`;
            for (let i = 1; i < path.length; i++) {
                const edge = line(path[i-1], path[i], `sov-map-route${route.valid ? "" : " invalid"}`, group);
                if (edge) edge.setAttribute("marker-end", "url(#sov-map-arrow)");
            }
        }
        for (const n of visible) {
            const [x,y] = point(n), active = n.id === selected, reachable = nearby.has(n.id);
            const group = svg("g", {transform:`translate(${x} ${y})`, role:"button", tabindex:0, "aria-label":`${n.name}${n.warnings?.length ? ", resource warnings" : ""}${reachable ? ", within 5 LY" : ""}`, "data-map-node":n.id});
            if (selected && !active && !reachable) group.setAttribute("opacity", ".42");
            if (reachable) svg("circle", {r:12,class:"sov-map-candidate","vector-effect":"non-scaling-stroke"}, group);
            if (active) svg("circle", {r:15,fill:"none",stroke:"var(--bs-body-color)","stroke-width":2}, group);
            svg("circle", {r:7, fill:colors.get(n.zone)||"var(--bs-secondary, #777)", stroke:enabled.warnings && n.warnings?.length ? "var(--bs-danger)" : "var(--bs-body-color)", "stroke-width":enabled.warnings && n.warnings?.length ? 3 : 1, "stroke-dasharray":n.planned_id ? "none" : "2 2"}, group);
            const title = svg("title", {}, group);
            title.textContent = `${n.name}\n${n.constellation}\nOwner: ${n.owner||"Outside plan; not captured"}\nCapital: ${distance(n.capital_distance)}${n.zone ? ` (Zone ${n.zone})` : ""}\n${n.warnings?.join("\n")||""}`;
            let labelY = 4;
            function label(text, extra = {}) { const el = svg("text", {x:19,y:labelY,"font-size":12,...extra},group); el.textContent = text; labelY+=16; }
            label(`${n.id===data.capital ? "★ " : ""}${n.name}${enabled.warnings && n.warnings?.length ? " ⚠" : ""}`);
            if (enabled.owners) label(n.owner||"Owner not captured", {"font-size":10});
            if (enabled.upgrades && n.upgrades?.length) label(n.upgrades.map(u => `${u.name} (${u.status})`).join(" · "), {"font-size":10});
        }
        const missing = [...nodes.values()].filter(n => !n.position).length;
        message.textContent = `${visible.length} systems mapped${missing ? `; ${missing} missing SDE 2D coordinates (still selectable in the menu)` : ""}. ${rangeLoading ? "Checking 5 LY range…" : rangeError}`;
        setView();
        if (focused) canvas.querySelector(`[data-map-node="${focused}"]`)?.focus({preventScroll:true});
    }
    function showSelection() {
        sidebar.replaceChildren();
        const n = nodeMap().get(selected);
        if (!n) { sidebar.append(element("p", "Select a system to inspect ownership, upgrades, workforce and connection candidates.")); return; }
        sidebar.append(element("h2", n.name, "h5"));
        sidebar.append(element("div", n.constellation, "text-body-secondary"));
        sidebar.append(element("p", `Capital distance: ${distance(n.capital_distance)}${n.zone ? ` · Zone ${n.zone}` : ""}`, "my-2"));
        if (!n.position) sidebar.append(element("p", "Missing 2D coordinates. Reload the SDE to place this system on the map.", "text-warning"));
        if (n.planned_id) {
            sidebar.append(element("div", `Owner: ${n.owner}`));
            if (n.owner_observed_at) sidebar.append(element("div", `Snapshot: ${new Date(n.owner_observed_at).toLocaleString()}`, "small text-body-secondary"));
            sidebar.append(element("div", `Power: ${number(n.power.left)} left / ${number(n.power.initial)} initial`, n.power.left < 0 ? "text-danger" : ""));
            sidebar.append(element("div", `Workforce: ${number(n.workforce.left)} left / ${number(n.workforce.initial)} initial`, n.workforce.left < 0 ? "text-danger" : ""));
            sidebar.append(element("div", `${n.mode} · Import ${number(n.workforce.imported)} / Export ${number(n.workforce.exported)} / Transit ${number(n.workforce.transit)}`));
            for (const warning of n.warnings) sidebar.append(element("div", `⚠ ${warning}`, "text-danger"));
            const actions = element("div", null, "d-flex flex-wrap gap-1 my-2");
            for (const [name,url] of Object.entries(n.actions)) editLink(name,url,actions);
            sidebar.append(actions, element("h3", "Upgrades", "h6 mt-2"));
            for (const u of n.upgrades) {
                const row = element("div", null, "border-top py-1");
                row.append(element("div", `${u.name} · ${u.status}`)); editLink("Edit", u.edit, row);
                if (u.install) {
                    const form = element("form", null, "d-inline ms-1"); form.method="post"; form.action=u.install; form.dataset.sovAction="";
                    const csrf = document.querySelector("#sov-board input[name=csrfmiddlewaretoken]");
                    if (csrf) form.append(csrf.cloneNode());
                    const button = element("button", "✓ Installed", "btn btn-sm btn-outline-success"); button.type="submit"; form.append(button); row.append(form);
                }
                sidebar.append(row);
            }
            if (!n.upgrades.length) sidebar.append(element("p", "No upgrades attached.", "text-body-secondary"));
            for (const r of data.routes.filter(r => r.path.includes(n.id) || r.source===n.id || r.destination===n.id)) {
                const row = element("div", null, "border-top py-1");
                row.append(element("div", `${nodeMap().get(r.source)?.name} → ${nodeMap().get(r.destination)?.name}: ${number(r.amount)} workforce${r.valid ? "" : " · Invalid route"}`));
                editLink("Edit route",r.edit,row); sidebar.append(row);
            }
        } else sidebar.append(element("p", "Outside this plan. Ownership, upgrades and editing are available only for systems added to the plan.", "text-body-secondary"));
        sidebar.append(element("h3", "Possible Ansiblex connections", "h6 mt-3"));
        sidebar.append(element("p", "Player-claimable nullsec in range. Both endpoints require the appropriate online infrastructure; ownership and existing links must be checked.", "small text-body-secondary"));
        if (n.planned_id) sidebar.append(element("div", `Advanced Logistics Network here: ${n.logistics || "not in plan"}`, "small"));
        if (rangeLoading || rangeError) sidebar.append(element("p", rangeLoading ? "Checking range…" : rangeError));
        else {
            sidebar.append(element("p", `${candidates.length} systems within 5 LY.`, "mb-1"));
            const list = element("div", null, "sov-map-nearby");
            for (const candidate of candidates) {
                const known = nodeMap().get(candidate.id), row = element("div", null, "border-top py-1");
                const button = element("button", candidate.name, "btn btn-link btn-sm p-0"); button.type="button"; button.dataset.mapSelect=candidate.id;
                row.append(button, element("span", ` · ${distance(candidate.source_distance)}${candidate.zone ? ` · Z${candidate.zone}` : ""}${known.planned_id ? ` · Logistics: ${known.logistics||"not in plan"}` : " · outside plan"}`));
                list.append(row);
            }
            sidebar.append(list);
        }
    }
    async function select(id) {
        const source = nodeMap().get(id);
        if (!source) return;
        selected = id; const sequence = ++rangeSequence;
        // Retain only the current out-of-plan source when replacing its neighbours.
        data.nodes = data.nodes.filter(n => n.planned_id);
        if (!source.planned_id) data.nodes.push(source);
        candidates = [];
        rangeLoading = true; rangeError = ""; chooser.value = String(id); draw(); showSelection();
        try {
            const result = await request(panel.dataset.rangeUrl.replace("/0/", `/${id}/`));
            if (sequence !== rangeSequence) return;
            candidates = result.nodes; rangeError = result.error || "";
        } catch (error) { if (sequence === rangeSequence) { candidates=[]; rangeError=error.message; } }
        finally { if (sequence === rangeSequence) { rangeLoading=false; draw(); showSelection(); } }
    }
    async function load() {
        const sequence = ++loadSequence; ++rangeSequence;
        message.textContent="Loading map…";
        try {
            const result = await request(panel.dataset.url);
            if (sequence !== loadSequence) return;
            const first = !data || !projected; data=result; dirty=false; candidates=[]; rangeLoading=false; rangeError="";
            const positions = data.nodes.filter(n=>n.position).map(n=>n.position);
            if (!projected && positions.length) {
                origin=positions[0];
                unit=Math.max(...positions.map(p=>Math.max(Math.abs(p[0]-origin[0]),Math.abs(p[1]-origin[1]))))/800 || 1;
                projected = true;
            }
            chooser.replaceChildren(element("option", "Select a system…")); chooser.firstChild.value="";
            for (const n of [...data.nodes].sort((a,b)=>a.name.localeCompare(b.name))) { const option=element("option",n.name); option.value=n.id; chooser.append(option); }
            document.getElementById("sov-map-capital").textContent=`Capital: ${data.capital_name||"not set"}`;
            if (first) fit();
            if (data.nodes.some(n=>n.id===selected)) await select(selected);
            else { selected=null; draw(); showSelection(); }
        } catch (error) { if (sequence === loadSequence) { dirty=true; message.textContent=error.message; } }
    }
    document.querySelectorAll("[data-sov-view]").forEach(button => button.addEventListener("click", () => {
        const map = button.dataset.sovView === "map";
        panel.hidden=!map; document.getElementById("sov-board").hidden=map;
        document.getElementById("system-filter").hidden=map;
        for (const item of document.querySelectorAll("[data-sov-view]")) { const active=item===button; item.setAttribute("aria-pressed",String(active)); item.className=`btn btn-sm ${active ? "btn-primary" : "btn-outline-primary"}`; }
        if (map && dirty) load();
    }));
    document.addEventListener("sov:updated", () => { dirty=true; if (!panel.hidden) load(); });
    function clearSelection() {
        ++rangeSequence; selected=null; candidates=[]; rangeError=""; rangeLoading=false;
        if (data) data.nodes=data.nodes.filter(n=>n.planned_id);
        chooser.value=""; draw(); showSelection();
    }
    chooser.addEventListener("change", () => { if (chooser.value) select(Number(chooser.value)); });
    panel.addEventListener("change", event => { if (event.target.matches("[data-map-layer]")) draw(); });
    panel.addEventListener("click", event => {
        const node=event.target.closest("[data-map-node], [data-map-select]");
        if (node && !moved) select(Number(node.dataset.mapNode||node.dataset.mapSelect));
        const fitButton=event.target.closest("[data-map-fit]"); if (fitButton) fit(fitButton.dataset.mapFit);
        const zoomButton=event.target.closest("[data-map-zoom]"); if (zoomButton) zoom(Number(zoomButton.dataset.mapZoom));
        if (event.target.closest("[data-map-clear]")) clearSelection();
    });
    canvas.addEventListener("keydown", event => {
        const node=event.target.closest("[data-map-node]");
        if (node && ["Enter"," "].includes(event.key)) {event.preventDefault(); select(Number(node.dataset.mapNode));}
        if (event.key==="Escape") clearSelection();
    });
    canvas.addEventListener("wheel", event => { event.preventDefault(); zoom(event.deltaY>0 ? 1.12 : 1/1.12); }, {passive:false});
    canvas.addEventListener("pointerdown", event => { if (event.button!==0) return; moved=false; drag={x:event.clientX,y:event.clientY,view:[...view]}; });
    canvas.addEventListener("pointermove", event => {
        if (!drag) return;
        const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
        if (Math.hypot(dx,dy)<4) return;
        moved=true; canvas.setPointerCapture(event.pointerId);
        const bounds=canvas.getBoundingClientRect(),scale=Math.max(drag.view[2]/bounds.width,drag.view[3]/bounds.height);
        view=[drag.view[0]-dx*scale,drag.view[1]-dy*scale,drag.view[2],drag.view[3]]; setView();
    });
    canvas.addEventListener("pointerup",()=>{drag=null; setTimeout(()=>{moved=false;},0);});
    canvas.addEventListener("pointercancel",()=>{drag=null;moved=false;});
    canvas.addEventListener("pointerleave",()=>{if(!moved)drag=null;});
})();
