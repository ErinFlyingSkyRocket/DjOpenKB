(function () {
    "use strict";

    var form = document.getElementById("edit_form");
    if (!form || !form.dataset.articleEditWorkspaceId) {
        return;
    }

    var workspaceId = form.dataset.articleEditWorkspaceId;
    var autosaveUrl = form.dataset.articleEditWorkspaceAutosaveUrl;
    var discardUrl = form.dataset.articleEditWorkspaceDiscardUrl;
    var fallbackUrl = form.dataset.articleEditWorkspaceFallbackUrl || "/home/";
    var resetUrl = form.dataset.articleEditWorkspaceResetUrl || form.action || fallbackUrl;
    var savingText = form.dataset.articleEditWorkspaceSavingText || "Saving edit checkpoint…";
    var savedText = form.dataset.articleEditWorkspaceSavedText || "Edit checkpoint saved.";
    var restoredText = form.dataset.articleEditWorkspaceRestoredText || "Edit checkpoint restored.";
    var saveErrorText = form.dataset.articleEditWorkspaceSaveErrorText || "The edit checkpoint could not be saved.";
    var discardErrorText = form.dataset.articleEditWorkspaceDiscardErrorText || "The edit checkpoint could not be discarded.";

    var csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
    var csrfToken = csrfInput ? csrfInput.value : "";
    var titleInput = document.getElementById("frm_kb_title");
    var keywordInput = document.getElementById("frm_kb_keywords");
    var visibilityInput = document.getElementById("articleVisibilitySelect") || form.querySelector('input[name="article_visibility"]');
    var statusInput = document.getElementById("articleStatusSelect");
    var reviewNotesInput = document.getElementById("reviewNotesInput");
    var textarea = document.getElementById("editor");
    var editorModeInput = form.querySelector('input[name="editor_mode"]');
    var statusElement = document.getElementById("articleEditWorkspaceStatus");

    var leaveModalElement = document.getElementById("articleEditWorkspaceLeaveModal");
    var leaveModalError = document.getElementById("articleEditWorkspaceLeaveError");
    var keepContinueButton = document.getElementById("articleEditWorkspaceKeepContinueButton");
    var discardContinueButton = document.getElementById("articleEditWorkspaceDiscardContinueButton");
    var resetButton = document.getElementById("articleEditWorkspaceResetButton");
    var resetModalElement = document.getElementById("articleEditWorkspaceResetModal");
    var resetConfirmButton = document.getElementById("articleEditWorkspaceResetConfirmButton");
    var resetModalError = document.getElementById("articleEditWorkspaceResetError");
    var reloadLatestButton = document.getElementById("articleEditReloadLatestButton");
    var reloadLatestError = document.getElementById("articleEditReloadLatestError");

    var dirty = form.dataset.articleEditWorkspaceDirty === "true";
    var bypassLeaveGuard = false;
    var autosaveTimer = null;
    var saveInFlight = false;
    var lastSavedSnapshot = null;
    var pendingNavigation = null;
    var allowLeaveModalHide = false;
    var historyGuardEnabled = Boolean(window.history && window.history.pushState);
    var sessionRedirecting = false;

    function getCodeMirror() {
        var wrapper = document.querySelector(".CodeMirror");
        return wrapper && wrapper.CodeMirror ? wrapper.CodeMirror : null;
    }

    function readBody() {
        var cm = getCodeMirror();
        return cm ? cm.getValue() : (textarea ? textarea.value : "");
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
        statusElement.textContent = message || "";
        statusElement.classList.toggle("is-error", state === "error");
        statusElement.classList.toggle("is-saving", state === "saving");
    }

    function sessionRedirectUrl(response) {
        if (!response) {
            return "";
        }
        if (response.redirected && response.url) {
            return response.url;
        }
        if (response.status === 401) {
            return fallbackUrl;
        }
        return "";
    }

    function redirectAfterSessionEnded(response) {
        var redirectUrl = sessionRedirectUrl(response);
        if (!redirectUrl) {
            return false;
        }
        sessionRedirecting = true;
        bypassLeaveGuard = true;
        dirty = false;
        pendingNavigation = null;
        window.clearTimeout(autosaveTimer);
        allowLeaveModalHide = true;
        window.location.replace(redirectUrl);
        return true;
    }

    function currentSnapshot() {
        syncBodyToTextarea();
        return JSON.stringify({
            title: titleInput ? titleInput.value : "",
            body: readBody(),
            keywords: keywordInput ? keywordInput.value : "",
            visibility: visibilityInput ? visibilityInput.value : "",
            status: statusInput ? statusInput.value : "",
            reviewNotes: reviewNotesInput ? reviewNotesInput.value : ""
        });
    }

    function buildFormData(snapshot, includeCsrf) {
        var values = JSON.parse(snapshot || currentSnapshot());
        var data = new FormData();
        data.append("edit_workspace_id", workspaceId);
        data.append("editor_mode", editorModeInput ? editorModeInput.value : "edit");
        data.append("frm_kb_title", values.title);
        data.append("frm_kb_body", values.body);
        data.append("frm_kb_keywords", values.keywords);
        data.append("article_visibility", values.visibility);
        data.append("status", values.status);
        data.append("review_notes", values.reviewNotes);
        if (includeCsrf && csrfToken) {
            data.append("csrfmiddlewaretoken", csrfToken);
        }
        return data;
    }

    function markChanged() {
        if (bypassLeaveGuard) {
            return;
        }
        dirty = true;
        form.dataset.articleEditWorkspaceDirty = "true";
        setStatus(savingText, "saving");
        window.clearTimeout(autosaveTimer);
        autosaveTimer = window.setTimeout(saveWorkspace, 400);
    }

    function snapshotNeedsSaving() {
        if (!dirty) {
            return false;
        }
        try {
            return currentSnapshot() !== lastSavedSnapshot;
        } catch (error) {
            return true;
        }
    }

    async function waitForSave() {
        if (!saveInFlight) {
            return;
        }
        await new Promise(function (resolve) {
            var timer = window.setInterval(function () {
                if (!saveInFlight) {
                    window.clearInterval(timer);
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
        if (saveInFlight) {
            await waitForSave();
        }
        var snapshot = currentSnapshot();
        if (snapshot === lastSavedSnapshot) {
            setStatus(savedText, "saved");
            return true;
        }

        saveInFlight = true;
        try {
            var response = await fetch(autosaveUrl, {
                method: "POST",
                headers: { "X-CSRFToken": csrfToken },
                credentials: "same-origin",
                body: buildFormData(snapshot, false)
            });
            if (redirectAfterSessionEnded(response)) {
                return false;
            }
            var data = {};
            try {
                data = await response.json();
            } catch (error) {
                data = {};
            }
            if (!response.ok || !data.saved) {
                throw new Error(data.error || saveErrorText);
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

    function flushForUnload() {
        if (bypassLeaveGuard || !dirty || !snapshotNeedsSaving()) {
            return;
        }
        window.clearTimeout(autosaveTimer);
        var data = buildFormData(currentSnapshot(), true);
        if (navigator.sendBeacon) {
            try {
                if (navigator.sendBeacon(autosaveUrl, data)) {
                    return;
                }
            } catch (error) {
                // Fall back to a keepalive request.
            }
        }
        try {
            fetch(autosaveUrl, {
                method: "POST",
                headers: { "X-CSRFToken": csrfToken },
                credentials: "same-origin",
                keepalive: true,
                body: data
            });
        } catch (error) {
            // The scheduled orphan cleanup remains the recovery safeguard.
        }
    }

    async function discardWorkspace() {
        var data = new FormData();
        data.append("edit_workspace_id", workspaceId);
        data.append("editor_mode", editorModeInput ? editorModeInput.value : "edit");
        var response = await fetch(discardUrl, {
            method: "POST",
            headers: { "X-CSRFToken": csrfToken },
            credentials: "same-origin",
            body: data
        });
        if (redirectAfterSessionEnded(response)) {
            return null;
        }
        var payload = {};
        try {
            payload = await response.json();
        } catch (error) {
            payload = {};
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

    lastSavedSnapshot = currentSnapshot();
    [titleInput, keywordInput, reviewNotesInput].forEach(function (input) {
        if (input) {
            input.addEventListener("input", markChanged);
        }
    });
    [visibilityInput, statusInput].forEach(function (input) {
        if (input) {
            input.addEventListener("change", markChanged);
        }
    });
    attachCodeMirrorListener();

    form.addEventListener("submit", function () {
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
            !dirty || bypassLeaveGuard || event.defaultPrevented || event.button !== 0 ||
            event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
        ) {
            return;
        }
        var link = event.target.closest ? event.target.closest("a[href]") : null;
        if (!link || link.closest("#articleEditWorkspaceLeaveModal") || link.closest("#articleEditWorkspaceResetModal")) {
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
                if (sessionRedirecting) {
                    return;
                }
                if (leaveModalError) {
                    leaveModalError.textContent = saveErrorText;
                    leaveModalError.classList.remove("csp-is-hidden");
                }
                setLeaveButtonsDisabled(false);
                return;
            }
            dirty = false;
            bypassLeaveGuard = true;
            hideLeaveModal();
            navigateAfterDecision();
        });
    }

    if (discardContinueButton) {
        discardContinueButton.addEventListener("click", async function () {
            setLeaveButtonsDisabled(true);
            try {
                await discardWorkspace();
                if (sessionRedirecting) {
                    return;
                }
                dirty = false;
                bypassLeaveGuard = true;
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
                await discardWorkspace();
                if (sessionRedirecting) {
                    return;
                }
                dirty = false;
                bypassLeaveGuard = true;
                window.clearTimeout(autosaveTimer);
                if (window.jQuery && resetModalElement) {
                    window.jQuery(resetModalElement).modal("hide");
                }
                var separator = resetUrl.indexOf("?") === -1 ? "?" : "&";
                window.location.replace(resetUrl + separator + "edit_workspace_reset=" + Date.now());
            } catch (error) {
                if (resetModalError) {
                    resetModalError.textContent = error.message || discardErrorText;
                    resetModalError.classList.remove("csp-is-hidden");
                }
                resetConfirmButton.disabled = false;
            }
        });
    }

    if (reloadLatestButton) {
        reloadLatestButton.addEventListener("click", async function () {
            reloadLatestButton.disabled = true;
            if (reloadLatestError) {
                reloadLatestError.textContent = "";
                reloadLatestError.classList.add("csp-is-hidden");
            }
            try {
                await discardWorkspace();
                if (sessionRedirecting) {
                    return;
                }
                dirty = false;
                bypassLeaveGuard = true;
                window.clearTimeout(autosaveTimer);
                var separator = resetUrl.indexOf("?") === -1 ? "?" : "&";
                window.location.replace(resetUrl + separator + "edit_workspace_reload=" + Date.now());
            } catch (error) {
                if (reloadLatestError) {
                    reloadLatestError.textContent = error.message || discardErrorText;
                    reloadLatestError.classList.remove("csp-is-hidden");
                }
                reloadLatestButton.disabled = false;
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
    window.addEventListener("pagehide", flushForUnload);
    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "hidden" && dirty && !bypassLeaveGuard) {
            saveWorkspace();
        }
    });

    if (historyGuardEnabled) {
        window.history.pushState({ articleEditWorkspaceGuard: true }, "", window.location.href);
        window.addEventListener("popstate", function () {
            if (bypassLeaveGuard) {
                return;
            }
            if (!dirty) {
                bypassLeaveGuard = true;
                window.history.back();
                return;
            }
            window.history.pushState({ articleEditWorkspaceGuard: true }, "", window.location.href);
            showLeaveModal({ type: "history" });
        });
    }

    if (dirty) {
        setStatus(restoredText, "saved");
    }
}());
