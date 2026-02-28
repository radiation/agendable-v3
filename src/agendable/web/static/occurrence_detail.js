(() => {
    const root = document.getElementById("occurrence-detail-root");
    if (!root) {
        return;
    }

    const panel = document.getElementById("occurrence-shared-panel");
    const status = document.getElementById("occurrence-sync-status");
    const error = document.getElementById("occurrence-sync-error");
    const taskForm = document.getElementById("task-capture-form");
    const agendaForm = document.getElementById("agenda-capture-form");
    const taskTitleInput = document.getElementById("task-title-input");
    const agendaBodyInput = document.getElementById("agenda-body-input");

    if (!panel || !status || !error) {
        return;
    }

    const captureFocusStorageKey = "occurrence-capture-focus";
    const hasTaskErrors = root.dataset.hasTaskErrors === "true";
    const hasAgendaErrors = root.dataset.hasAgendaErrors === "true";
    const isOccurrenceCompleted = root.dataset.occurrenceCompleted === "true";

    const liveSectionIds = [
        "occurrence-live-attendees",
        "occurrence-live-tasks",
        "occurrence-live-agenda",
    ];
    const freshnessSectionIds = [
        "occurrence-live-tasks",
        "occurrence-live-agenda",
    ];
    const rowFreshnessMs = 7000;
    const syncAgeTickMs = 1000;

    let syncState = "idle";
    let lastSuccessfulSyncAt = null;
    let previousRowSnapshots = collectRowSnapshots();
    let requestRowSnapshots = null;

    function clearError() {
        error.textContent = "";
    }

    function formatSyncAgeText(nowMs) {
        if (lastSuccessfulSyncAt === null) {
            return "Up to date";
        }
        const elapsedSeconds = Math.max(0, Math.floor((nowMs - lastSuccessfulSyncAt) / 1000));
        if (elapsedSeconds <= 1) {
            return "Updated just now";
        }
        return `Updated ${elapsedSeconds}s ago`;
    }

    function renderSyncStatus() {
        if (syncState === "syncing") {
            status.textContent = "Syncing…";
            return;
        }
        if (syncState === "error") {
            status.textContent = "Sync paused";
            return;
        }
        status.textContent = formatSyncAgeText(Date.now());
    }

    function markUpdated() {
        lastSuccessfulSyncAt = Date.now();
        syncState = "fresh";
        renderSyncStatus();
        for (const id of liveSectionIds) {
            const section = document.getElementById(id);
            if (!section) {
                continue;
            }
            section.classList.remove("live-updated");
            void section.offsetWidth;
            section.classList.add("live-updated");
        }
    }

    function snapshotSectionRows(sectionId) {
        const snapshot = new Map();
        const section = document.getElementById(sectionId);
        if (!section) {
            return snapshot;
        }

        const rows = section.querySelectorAll("[data-live-key]");
        for (const row of rows) {
            const key = row.getAttribute("data-live-key");
            if (!key) {
                continue;
            }
            snapshot.set(key, row.getAttribute("data-live-signature") || "");
        }
        return snapshot;
    }

    function collectRowSnapshots() {
        const snapshots = {};
        for (const sectionId of freshnessSectionIds) {
            snapshots[sectionId] = snapshotSectionRows(sectionId);
        }
        return snapshots;
    }

    function clearRowFreshness(section) {
        const existingFreshRows = section.querySelectorAll(".row-fresh");
        for (const row of existingFreshRows) {
            row.classList.remove("row-fresh");
            row.removeAttribute("data-freshness-token");
        }
        const existingBadges = section.querySelectorAll(".freshness-badge");
        for (const badge of existingBadges) {
            badge.remove();
        }
    }

    function scheduleFreshnessExpiry(row, badge, token) {
        window.setTimeout(() => {
            if (row.getAttribute("data-freshness-token") !== token) {
                return;
            }
            row.classList.remove("row-fresh");
            row.removeAttribute("data-freshness-token");
            badge.remove();
        }, rowFreshnessMs);
    }

    function markFreshRowsFromSnapshot(sectionId, baselineSnapshot) {
        const section = document.getElementById(sectionId);
        if (!section) {
            return;
        }
        clearRowFreshness(section);

        const rows = section.querySelectorAll("[data-live-key]");
        for (const row of rows) {
            const key = row.getAttribute("data-live-key");
            if (!key) {
                continue;
            }
            const nextSignature = row.getAttribute("data-live-signature") || "";
            const previousSignature = baselineSnapshot.get(key);
            if (previousSignature === nextSignature) {
                continue;
            }

            row.classList.add("row-fresh");
            const badge = document.createElement("small");
            badge.className = "freshness-badge";
            badge.textContent = "Updated just now";
            row.appendChild(badge);
            const token = String(Date.now());
            row.setAttribute("data-freshness-token", token);
            scheduleFreshnessExpiry(row, badge, token);
        }
    }

    function markRowFreshness() {
        const baseline = requestRowSnapshots || previousRowSnapshots;
        for (const sectionId of freshnessSectionIds) {
            const baselineSnapshot = baseline[sectionId] || new Map();
            markFreshRowsFromSnapshot(sectionId, baselineSnapshot);
        }
        previousRowSnapshots = collectRowSnapshots();
        requestRowSnapshots = null;
    }

    document.body.addEventListener("htmx:beforeRequest", (event) => {
        if (event.detail.elt !== panel) {
            return;
        }
        requestRowSnapshots = collectRowSnapshots();
        syncState = "syncing";
        renderSyncStatus();
    });

    document.body.addEventListener("htmx:afterSwap", (event) => {
        if (event.detail.target !== panel) {
            return;
        }
        clearError();
        markUpdated();
        markRowFreshness();
    });

    document.body.addEventListener("htmx:responseError", (event) => {
        if (event.detail.elt !== panel) {
            return;
        }
        requestRowSnapshots = null;
        syncState = "error";
        renderSyncStatus();
        error.textContent = "Could not refresh shared updates.";
    });

    window.setInterval(renderSyncStatus, syncAgeTickMs);

    function focusElement(element) {
        if (!(element instanceof HTMLElement)) {
            return;
        }
        element.focus();
        if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
            element.select();
        }
    }

    function focusCapture(kind) {
        if (kind === "agenda") {
            focusElement(agendaBodyInput);
            return;
        }
        focusElement(taskTitleInput);
    }

    function isEditableTarget(target) {
        if (!(target instanceof HTMLElement)) {
            return false;
        }
        if (target.isContentEditable) {
            return true;
        }
        const tag = target.tagName;
        return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    }

    function rememberCaptureTarget(kind) {
        try {
            window.sessionStorage.setItem(captureFocusStorageKey, kind);
        } catch {
            return;
        }
    }

    if (taskForm instanceof HTMLFormElement) {
        taskForm.addEventListener("submit", () => {
            rememberCaptureTarget("task");
        });
    }

    if (agendaForm instanceof HTMLFormElement) {
        agendaForm.addEventListener("submit", () => {
            rememberCaptureTarget("agenda");
        });
    }

    let rememberedTarget = null;
    try {
        rememberedTarget = window.sessionStorage.getItem(captureFocusStorageKey);
        if (rememberedTarget !== null) {
            window.sessionStorage.removeItem(captureFocusStorageKey);
        }
    } catch {
        rememberedTarget = null;
    }

    if (!isOccurrenceCompleted) {
        if (hasTaskErrors) {
            focusCapture("task");
        } else if (hasAgendaErrors) {
            focusCapture("agenda");
        } else if (rememberedTarget === "agenda" || rememberedTarget === "task") {
            focusCapture(rememberedTarget);
        }
    }

    document.addEventListener("keydown", (event) => {
        if (isOccurrenceCompleted) {
            return;
        }

        const key = event.key.toLowerCase();
        const target = event.target;

        if ((event.metaKey || event.ctrlKey) && key === "k") {
            event.preventDefault();
            focusCapture("task");
            return;
        }

        if (event.altKey && !event.metaKey && !event.ctrlKey) {
            if (key === "t") {
                event.preventDefault();
                focusCapture("task");
                return;
            }
            if (key === "a") {
                event.preventDefault();
                focusCapture("agenda");
                return;
            }
        }

        if (event.key !== "Enter" || event.shiftKey) {
            return;
        }
        if (!(target instanceof HTMLElement)) {
            return;
        }

        const form = target.closest("form[data-capture-kind]");
        if (!(form instanceof HTMLFormElement)) {
            return;
        }
        if (target.tagName === "TEXTAREA") {
            return;
        }
        if (!isEditableTarget(target) && target.tagName !== "BUTTON") {
            return;
        }

        event.preventDefault();
        form.requestSubmit();
    });
})();
