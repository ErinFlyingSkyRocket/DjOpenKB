(function () {
    "use strict";

    var form = document.getElementById("edit_form");
    if (!form || !form.dataset.articleWorkspaceId) {
        return;
    }

    var workspaceId = form.dataset.articleWorkspaceId;
    var autosaveUrl = form.dataset.articleWorkspaceAutosaveUrl;
    var discardUrl = form.dataset.articleWorkspaceDiscardUrl;
    var fallbackUrl = form.dataset.articleWorkspaceFallbackUrl || "/home/";
    var resetUrl = form.dataset.articleWorkspaceResetUrl || form.action || fallbackUrl;
    var savingText = form.dataset.articleWorkspaceSavingText || "Saving checkpoint…";
    var savedText = form.dataset.articleWorkspaceSavedText || "Checkpoint saved. You can continue this article later.";
    var restoredText = form.dataset.articleWorkspaceRestoredText || "Checkpoint restored. Use Reset article to start again with a blank editor.";
    var saveErrorText = form.dataset.articleWorkspaceSaveErrorText || "The checkpoint could not be saved.";
    var discardErrorText = form.dataset.articleWorkspaceDiscardErrorText || "The temporary article could not be discarded.";
    var conflictText = form.dataset.articleWorkspaceConflictText || "This checkpoint was updated in another tab. Reload the page to use the newest version.";
    var csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
    var csrfToken = csrfInput ? csrfInput.value : "";
    var titleInput = document.getElementById("frm_kb_title");
    var keywordInput = document.getElementById("frm_kb_keywords");
    var visibilityInput = document.getElementById("articleVisibilitySelect") || form.querySelector('input[name="article_visibility"]');
    var textarea = document.getElementById("editor");
    var revisionInput = form.querySelector('input[name="workspace_revision"]');
    var editorTokenInput = form.querySelector('input[name="workspace_editor_token"]');
    var saveSequenceInput = form.querySelector('input[name="workspace_save_sequence"]');
    var statusElement = document.getElementById("articleWorkspaceStatus");
    var leaveModalElement = document.getElementById("articleWorkspaceLeaveModal");
    var leaveModalError = document.getElementById("articleWorkspaceLeaveError");
    var discardContinueButton = document.getElementById("articleWorkspaceDiscardContinueButton");
    var keepContinueButton = document.getElementById("articleWorkspaceKeepContinueButton");
    var resetButton = document.getElementById("articleWorkspaceResetButton");
    var resetModalElement = document.getElementById("articleWorkspaceResetModal");
    var resetConfirmButton = document.getElementById("articleWorkspaceResetConfirmButton");
    var resetModalError = document.getElementById("articleWorkspaceResetError");
    var dirty = form.dataset.articleWorkspaceDirty === "true";
    var bypassLeaveGuard = false;
    var autosaveTimer = null;
    var saveInFlight = false;
    var lastSavedSnapshot = null;
    var pendingNavigation = null;
    var historyGuardEnabled = Boolean(window.history && window.history.pushState);
    var allowLeaveModalHide = false;
    var workspaceRevision = parseInt(form.dataset.articleWorkspaceRevision || (revisionInput ? revisionInput.value : "0"), 10);
    if (!Number.isFinite(workspaceRevision) || workspaceRevision < 0) {
        workspaceRevision = 0;
    }
    var editorToken = "";
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
        editorToken = window.crypto.randomUUID();
    } else {
        editorToken = "editor-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
    }
    editorToken = editorToken.slice(0, 64);
    var saveSequence = 0;
    var conflictDetected = false;

    function syncVersionInputs() {
        if (revisionInput) {
            revisionInput.value = String(workspaceRevision);
        }
        if (editorTokenInput) {
            editorTokenInput.value = editorToken;
        }
        if (saveSequenceInput) {
            saveSequenceInput.value = String(saveSequence);
        }
    }

    function nextSaveSequence() {
        saveSequence += 1;
        syncVersionInputs();
        return saveSequence;
    }

    syncVersionInputs();

    function getCodeMirror() {
        var wrapper = document.querySelector(".CodeMirror");
        return wrapper && wrapper.CodeMirror ? wrapper.CodeMirror : null;
    }

    function readBody() {
        var cm = getCodeMirror();
        if (cm) {
            return cm.getValue();
        }
        return textarea ? textarea.value : "";
    }

    function syncBodyToTextarea() {
        var cm = getCodeMirror();
        if (cm) {
            cm.save();
        }
    }

    function setStatus(message, state) {
        if (!statusElement) {
            return;
        }
        statusElement.textContent = message;
        statusElement.classList.toggle("is-error", state === "error");
        statusElement.classList.toggle("is-saving", state === "saving");
    }

    function currentVisibility() {
        return visibilityInput ? visibilityInput.value : "public";
    }

    function currentWorkspaceSnapshot() {
        syncBodyToTextarea();
        return JSON.stringify({
            title: titleInput ? titleInput.value : "",
            body: readBody(),
            keywords: keywordInput ? keywordInput.value : "",
            visibility: currentVisibility()
        });
    }

    function buildWorkspaceFormData(snapshot, includeFormCsrfToken, sequence) {
        var values = JSON.parse(snapshot || currentWorkspaceSnapshot());
        var data = new FormData();
        data.append("workspace_id", workspaceId);
        data.append("frm_kb_title", values.title);
        data.append("frm_kb_body", values.body);
        data.append("frm_kb_keywords", values.keywords);
        data.append("article_visibility", values.visibility);
        data.append("workspace_revision", String(workspaceRevision));
        data.append("workspace_editor_token", editorToken);
        data.append("workspace_save_sequence", String(sequence));
        if (includeFormCsrfToken && csrfToken) {
            data.append("csrfmiddlewaretoken", csrfToken);
        }
        return data;
    }

    function scheduleAutosave() {
        if (bypassLeaveGuard || conflictDetected) {
            return;
        }
        window.clearTimeout(autosaveTimer);
        autosaveTimer = window.setTimeout(saveWorkspace, 400);
    }

    function markChanged() {
        if (bypassLeaveGuard) {
            return;
        }
        dirty = true;
        form.dataset.articleWorkspaceDirty = "true";
        if (conflictDetected) {
            setStatus(conflictText, "error");
            return;
        }
        setStatus(savingText, "saving");
        scheduleAutosave();
    }

    function snapshotNeedsSaving() {
        if (!dirty) {
            return false;
        }
        try {
            return currentWorkspaceSnapshot() !== lastSavedSnapshot;
        } catch (error) {
            return true;
        }
    }

    async function waitForAutosaveToFinish() {
        window.clearTimeout(autosaveTimer);
        if (!saveInFlight) {
            return;
        }
        await new Promise(function (resolve) {
            var waitTimer = window.setInterval(function () {
                if (!saveInFlight) {
                    window.clearInterval(waitTimer);
                    resolve();
                }
            }, 50);
        });
    }

    async function saveWorkspace() {
        window.clearTimeout(autosaveTimer);
        if (bypassLeaveGuard || !dirty) {
            return true;
        }
        if (conflictDetected) {
            setStatus(conflictText, "error");
            return false;
        }
        if (saveInFlight) {
            await waitForAutosaveToFinish();
        }

        var snapshot = currentWorkspaceSnapshot();
        if (snapshot === lastSavedSnapshot) {
            return true;
        }

        var sequence = nextSaveSequence();
        saveInFlight = true;
        try {
            var response = await fetch(autosaveUrl, {
                method: "POST",
                headers: { "X-CSRFToken": csrfToken },
                credentials: "same-origin",
                body: buildWorkspaceFormData(snapshot, false, sequence)
            });
            var data = {};
            try {
                data = await response.json();
            } catch (error) {
                data = {};
            }
            if (response.status === 409 || data.conflict) {
                conflictDetected = true;
                throw new Error(data.error || conflictText);
            }
            if (!response.ok || !data.saved) {
                throw new Error(data.error || saveErrorText);
            }
            var returnedRevision = parseInt(data.revision, 10);
            if (Number.isFinite(returnedRevision) && returnedRevision >= 0) {
                workspaceRevision = returnedRevision;
                syncVersionInputs();
            }
            lastSavedSnapshot = snapshot;
            setStatus(savedText, "saved");
            return true;
        } catch (error) {
            setStatus(error.message || saveErrorText, "error");
            return false;
        } finally {
            saveInFlight = false;
        }
    }

    function flushWorkspaceCheckpointForUnload() {
        if (bypassLeaveGuard || conflictDetected || !dirty || !snapshotNeedsSaving()) {
            return;
        }
        window.clearTimeout(autosaveTimer);
        var snapshot = currentWorkspaceSnapshot();
        var sequence = nextSaveSequence();
        var data = buildWorkspaceFormData(snapshot, true, sequence);

        if (navigator.sendBeacon) {
            try {
                if (navigator.sendBeacon(autosaveUrl, data)) {
                    return;
                }
            } catch (error) {
                // Fall through to keepalive fetch when the browser rejects the beacon.
            }
        }

        try {
            fetch(autosaveUrl, {
                method: "POST",
                headers: { "X-CSRFToken": csrfToken },
                credentials: "same-origin",
                body: buildWorkspaceFormData(snapshot, false, sequence),
                keepalive: true
            }).catch(function () {});
        } catch (error) {
            // The browser-native leave warning remains the final protection.
        }
    }

    async function discardWorkspace(options) {
        options = options || {};
        await waitForAutosaveToFinish();
        var data = new FormData();
        var sequence = nextSaveSequence();
        data.append("workspace_id", workspaceId);
        data.append("workspace_revision", String(workspaceRevision));
        data.append("workspace_editor_token", editorToken);
        data.append("workspace_save_sequence", String(sequence));
        if (options.resetLatest) {
            data.append("reset_latest", "1");
        }
        var response = await fetch(discardUrl, {
            method: "POST",
            headers: { "X-CSRFToken": csrfToken },
            credentials: "same-origin",
            body: data
        });
        var payload = {};
        try {
            payload = await response.json();
        } catch (error) {
            payload = {};
        }
        if (response.status === 409 || payload.conflict) {
            conflictDetected = true;
            throw new Error(payload.error || conflictText);
        }
        if (!response.ok || !payload.discarded) {
            throw new Error(payload.error || discardErrorText);
        }
        return payload;
    }

    function attachCodeMirrorListener() {
        var attempts = 0;
        var timer = window.setInterval(function () {
            attempts += 1;
            var cm = getCodeMirror();
            if (cm) {
                window.clearInterval(timer);
                cm.on("change", markChanged);
            } else if (attempts >= 80) {
                window.clearInterval(timer);
                if (textarea) {
                    textarea.addEventListener("input", markChanged);
                }
            }
        }, 100);
    }

    lastSavedSnapshot = currentWorkspaceSnapshot();

    [titleInput, keywordInput].forEach(function (input) {
        if (input) {
            input.addEventListener("input", markChanged);
        }
    });
    if (visibilityInput) {
        visibilityInput.addEventListener("change", markChanged);
    }
    attachCodeMirrorListener();

    form.addEventListener("submit", function (event) {
        if (conflictDetected) {
            event.preventDefault();
            setStatus(conflictText, "error");
            return;
        }
        nextSaveSequence();
        bypassLeaveGuard = true;
        dirty = false;
        window.clearTimeout(autosaveTimer);
        syncBodyToTextarea();
    });

    function showLeaveModal(navigation) {
        pendingNavigation = navigation;
        allowLeaveModalHide = false;
        if (leaveModalError) {
            leaveModalError.textContent = "";
            leaveModalError.classList.add("csp-is-hidden");
        }
        if (window.jQuery && leaveModalElement) {
            window.jQuery(leaveModalElement).modal({
                backdrop: "static",
                keyboard: false,
                show: true
            });
        }
    }

    function hideLeaveModal() {
        allowLeaveModalHide = true;
        if (window.jQuery && leaveModalElement) {
            window.jQuery(leaveModalElement).modal("hide");
        }
    }

    function navigateAfterDecision() {
        var navigation = pendingNavigation;
        pendingNavigation = null;
        if (!navigation) {
            window.location.href = fallbackUrl;
            return;
        }
        if (navigation.type === "history") {
            window.history.go(-2);
            window.setTimeout(function () {
                if (document.visibilityState === "visible") {
                    window.location.href = fallbackUrl;
                }
            }, 500);
            return;
        }
        if (navigation.type === "form" && navigation.form) {
            if (navigation.submitter && typeof navigation.form.requestSubmit === "function") {
                navigation.form.requestSubmit(navigation.submitter);
            } else {
                navigation.form.submit();
            }
            return;
        }
        window.location.href = navigation.url || fallbackUrl;
    }

    function setLeaveButtonsDisabled(disabled) {
        if (keepContinueButton) {
            keepContinueButton.disabled = disabled;
        }
        if (discardContinueButton) {
            discardContinueButton.disabled = disabled;
        }
    }

    document.addEventListener("click", function (event) {
        if (
            !dirty
            || bypassLeaveGuard
            || event.defaultPrevented
            || event.button !== 0
            || event.ctrlKey
            || event.metaKey
            || event.shiftKey
            || event.altKey
        ) {
            return;
        }
        var link = event.target.closest ? event.target.closest("a[href]") : null;
        if (!link || link.closest("#articleWorkspaceLeaveModal") || link.closest("#articleWorkspaceResetModal")) {
            return;
        }
        if (link.target === "_blank" || link.hasAttribute("download")) {
            return;
        }
        var href = link.getAttribute("href") || "";
        if (!href || href.charAt(0) === "#" || href.toLowerCase().indexOf("javascript:") === 0) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        showLeaveModal({ type: "url", url: link.href });
    }, true);

    document.addEventListener("submit", function (event) {
        if (!dirty || bypassLeaveGuard || event.target === form) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        showLeaveModal({
            type: "form",
            form: event.target,
            submitter: event.submitter || null
        });
    }, true);

    if (keepContinueButton) {
        keepContinueButton.addEventListener("click", async function () {
            setLeaveButtonsDisabled(true);
            var saved = await saveWorkspace();
            if (!saved) {
                if (leaveModalError) {
                    leaveModalError.textContent = conflictDetected ? conflictText : saveErrorText;
                    leaveModalError.classList.remove("csp-is-hidden");
                }
                setLeaveButtonsDisabled(false);
                return;
            }
            dirty = false;
            bypassLeaveGuard = true;
            window.clearTimeout(autosaveTimer);
            hideLeaveModal();
            navigateAfterDecision();
        });
    }

    if (discardContinueButton) {
        discardContinueButton.addEventListener("click", async function () {
            setLeaveButtonsDisabled(true);
            try {
                await discardWorkspace();
                dirty = false;
                bypassLeaveGuard = true;
                window.clearTimeout(autosaveTimer);
                hideLeaveModal();
                navigateAfterDecision();
            } catch (error) {
                if (leaveModalError) {
                    leaveModalError.textContent = error.message || discardErrorText;
                    leaveModalError.classList.remove("csp-is-hidden");
                }
                setLeaveButtonsDisabled(false);
            }
        });
    }

    if (window.jQuery && leaveModalElement) {
        window.jQuery(leaveModalElement).on("hide.bs.modal", function (event) {
            if (!allowLeaveModalHide) {
                event.preventDefault();
            }
        });
        window.jQuery(leaveModalElement).on("hidden.bs.modal", function () {
            allowLeaveModalHide = false;
            setLeaveButtonsDisabled(false);
        });
    }

    if (resetButton && window.jQuery && resetModalElement) {
        resetButton.addEventListener("click", function () {
            if (resetModalError) {
                resetModalError.textContent = "";
                resetModalError.classList.add("csp-is-hidden");
            }
            window.jQuery(resetModalElement).modal("show");
        });
    }

    if (resetConfirmButton) {
        resetConfirmButton.addEventListener("click", async function () {
            resetConfirmButton.disabled = true;
            try {
                // Reset is an explicit destructive action.  Remove the latest
                // server checkpoint even when another tab advanced its revision,
                // then replace this page with a freshly requested blank workspace.
                await discardWorkspace({ resetLatest: true });
                dirty = false;
                bypassLeaveGuard = true;
                conflictDetected = false;
                window.clearTimeout(autosaveTimer);
                if (window.jQuery && resetModalElement) {
                    window.jQuery(resetModalElement).modal("hide");
                }
                var separator = resetUrl.indexOf("?") === -1 ? "?" : "&";
                window.location.replace(resetUrl + separator + "workspace_reset=" + Date.now());
            } catch (error) {
                if (resetModalError) {
                    resetModalError.textContent = error.message || discardErrorText;
                    resetModalError.classList.remove("csp-is-hidden");
                }
                resetConfirmButton.disabled = false;
            }
        });
    }

    if (window.jQuery && resetModalElement) {
        window.jQuery(resetModalElement).on("hidden.bs.modal", function () {
            if (resetConfirmButton) {
                resetConfirmButton.disabled = false;
            }
        });
    }

    window.addEventListener("beforeunload", function (event) {
        if (bypassLeaveGuard || !dirty || !snapshotNeedsSaving()) {
            return;
        }
        event.preventDefault();
        event.returnValue = "";
    });

    window.addEventListener("pagehide", flushWorkspaceCheckpointForUnload);
    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "hidden" && dirty && !bypassLeaveGuard) {
            saveWorkspace();
        }
    });

    if (historyGuardEnabled) {
        window.history.pushState({ articleWorkspaceGuard: true }, "", window.location.href);
        window.addEventListener("popstate", function () {
            if (bypassLeaveGuard) {
                return;
            }
            if (!dirty) {
                bypassLeaveGuard = true;
                window.history.back();
                return;
            }
            window.history.pushState({ articleWorkspaceGuard: true }, "", window.location.href);
            showLeaveModal({ type: "history" });
        });
    }

    if (dirty) {
        setStatus(restoredText, "saved");
    }
}());
