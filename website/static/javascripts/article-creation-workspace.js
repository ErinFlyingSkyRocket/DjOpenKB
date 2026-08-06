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
    var savingText = form.dataset.articleWorkspaceSavingText || "Saving temporary workspace…";
    var savedText = form.dataset.articleWorkspaceSavedText || "Temporary workspace saved. Use Save draft to keep it in My Articles.";
    var restoredText = form.dataset.articleWorkspaceRestoredText || "Temporary workspace restored. Save it as a draft or discard it when finished.";
    var saveErrorText = form.dataset.articleWorkspaceSaveErrorText || "Temporary workspace could not be saved.";
    var discardErrorText = form.dataset.articleWorkspaceDiscardErrorText || "The temporary article could not be discarded.";
    var csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
    var csrfToken = csrfInput ? csrfInput.value : "";
    var titleInput = document.getElementById("frm_kb_title");
    var keywordInput = document.getElementById("frm_kb_keywords");
    var visibilityInput = document.getElementById("articleVisibilitySelect") || form.querySelector('input[name="article_visibility"]');
    var textarea = document.getElementById("editor");
    var statusElement = document.getElementById("articleWorkspaceStatus");
    var modalElement = document.getElementById("articleWorkspaceLeaveModal");
    var modalError = document.getElementById("articleWorkspaceLeaveError");
    var discardButton = document.getElementById("articleWorkspaceDiscardButton");
    var saveDraftButton = document.getElementById("articleWorkspaceSaveDraftButton");
    var stayButton = document.getElementById("articleWorkspaceStayButton");
    var dirty = form.dataset.articleWorkspaceDirty === "true";
    var bypassLeaveGuard = false;
    var autosaveTimer = null;
    var saveInFlight = false;
    var saveAgain = false;
    var lastSavedSnapshot = null;
    var pendingNavigation = null;
    var historyGuardEnabled = Boolean(window.history && window.history.pushState);
    var allowModalHide = false;

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

    function buildWorkspaceFormData(snapshot) {
        var values = JSON.parse(snapshot || currentWorkspaceSnapshot());
        var data = new FormData();
        data.append("workspace_id", workspaceId);
        data.append("frm_kb_title", values.title);
        data.append("frm_kb_body", values.body);
        data.append("frm_kb_keywords", values.keywords);
        data.append("article_visibility", values.visibility);
        return data;
    }

    function scheduleAutosave() {
        if (bypassLeaveGuard) {
            return;
        }
        window.clearTimeout(autosaveTimer);
        autosaveTimer = window.setTimeout(saveWorkspace, 650);
    }

    function markChanged() {
        if (bypassLeaveGuard) {
            return;
        }
        dirty = true;
        form.dataset.articleWorkspaceDirty = "true";
        setStatus(savingText, "saving");
        scheduleAutosave();
    }

    async function saveWorkspace() {
        window.clearTimeout(autosaveTimer);
        if (bypassLeaveGuard || !dirty) {
            return true;
        }
        if (saveInFlight) {
            saveAgain = true;
            await new Promise(function (resolve) {
                var waitTimer = window.setInterval(function () {
                    if (!saveInFlight) {
                        window.clearInterval(waitTimer);
                        resolve();
                    }
                }, 50);
            });
            return saveWorkspace();
        }

        var snapshot = currentWorkspaceSnapshot();
        if (snapshot === lastSavedSnapshot) {
            return true;
        }

        saveInFlight = true;
        saveAgain = false;
        var succeeded = false;
        try {
            var response = await fetch(autosaveUrl, {
                method: "POST",
                headers: { "X-CSRFToken": csrfToken },
                credentials: "same-origin",
                body: buildWorkspaceFormData(snapshot)
            });
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
            succeeded = true;
        } catch (error) {
            setStatus(error.message || saveErrorText, "error");
            succeeded = false;
        } finally {
            saveInFlight = false;
            saveAgain = false;
        }
        return succeeded;
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

    async function saveWorkspaceAsDraft() {
        await waitForAutosaveToFinish();
        syncBodyToTextarea();

        var data = new FormData(form);
        data.set("workspace_id", workspaceId);
        data.set("submit_action", "draft");
        data.set("workspace_leave_action", "save_draft");

        var response = await fetch(form.action, {
            method: "POST",
            headers: {
                "X-CSRFToken": csrfToken,
                "X-Requested-With": "XMLHttpRequest"
            },
            credentials: "same-origin",
            body: data
        });
        var payload = {};
        try {
            payload = await response.json();
        } catch (error) {
            payload = {};
        }
        if (!response.ok || !payload.saved) {
            throw new Error(payload.error || saveErrorText);
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

    form.addEventListener("submit", function () {
        bypassLeaveGuard = true;
        dirty = false;
        window.clearTimeout(autosaveTimer);
        syncBodyToTextarea();
    });

    function showLeaveModal(navigation) {
        pendingNavigation = navigation;
        allowModalHide = false;
        if (modalError) {
            modalError.textContent = "";
            modalError.classList.add("csp-is-hidden");
        }
        if (window.jQuery && modalElement) {
            window.jQuery(modalElement).modal({
                backdrop: "static",
                keyboard: false,
                show: true
            });
        }
    }

    function hideLeaveModal() {
        allowModalHide = true;
        if (window.jQuery && modalElement) {
            window.jQuery(modalElement).modal("hide");
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
        if (!link || link.closest("#articleWorkspaceLeaveModal")) {
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

    if (saveDraftButton) {
        saveDraftButton.addEventListener("click", async function () {
            saveDraftButton.disabled = true;
            if (discardButton) {
                discardButton.disabled = true;
            }
            if (stayButton) {
                stayButton.disabled = true;
            }
            try {
                await saveWorkspaceAsDraft();
                dirty = false;
                bypassLeaveGuard = true;
                window.clearTimeout(autosaveTimer);
                hideLeaveModal();
                navigateAfterDecision();
            } catch (error) {
                if (modalError) {
                    modalError.textContent = error.message || saveErrorText;
                    modalError.classList.remove("csp-is-hidden");
                }
                saveDraftButton.disabled = false;
                if (discardButton) {
                    discardButton.disabled = false;
                }
                if (stayButton) {
                    stayButton.disabled = false;
                }
            }
        });
    }

    if (discardButton) {
        discardButton.addEventListener("click", async function () {
            discardButton.disabled = true;
            if (saveDraftButton) {
                saveDraftButton.disabled = true;
            }
            try {
                var data = new FormData();
                data.append("workspace_id", workspaceId);
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
                if (!response.ok || !payload.discarded) {
                    throw new Error(payload.error || discardErrorText);
                }
                dirty = false;
                bypassLeaveGuard = true;
                window.clearTimeout(autosaveTimer);
                hideLeaveModal();
                navigateAfterDecision();
            } catch (error) {
                if (modalError) {
                    modalError.textContent = error.message || discardErrorText;
                    modalError.classList.remove("csp-is-hidden");
                }
                discardButton.disabled = false;
                if (saveDraftButton) {
                    saveDraftButton.disabled = false;
                }
            }
        });
    }

    if (stayButton) {
        stayButton.addEventListener("click", function () {
            pendingNavigation = null;
            hideLeaveModal();
            window.setTimeout(function () {
                allowModalHide = false;
            }, 0);
        });
    }

    if (window.jQuery && modalElement) {
        window.jQuery(modalElement).on("hide.bs.modal", function (event) {
            if (!allowModalHide) {
                event.preventDefault();
            }
        });
        window.jQuery(modalElement).on("hidden.bs.modal", function () {
            allowModalHide = false;
        });
    }

    window.addEventListener("beforeunload", function (event) {
        if (!dirty || bypassLeaveGuard) {
            return;
        }
        event.preventDefault();
        event.returnValue = "";
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
