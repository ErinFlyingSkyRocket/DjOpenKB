(function () {
    "use strict";

    var widget = document.getElementById("openkbAiWidget");
    if (!widget) { return; }

    var chat = document.getElementById("openkbAiChat");
    var toggle = document.getElementById("openkbAiToggle");
    var closeButton = document.getElementById("openkbAiClose");
    var clearButton = document.getElementById("openkbAiClear");
    var form = document.getElementById("openkbAiForm");
    var questionInput = document.getElementById("openkbAiQuestion");
    var sendButton = document.getElementById("openkbAiSend");
    var status = document.getElementById("openkbAiStatus");
    var thread = document.getElementById("openkbAiThread");
    var csrfInput = form ? form.querySelector("[name=csrfmiddlewaretoken]") : null;

    if (!chat || !toggle || !closeButton || !clearButton || !form || !questionInput || !sendButton || !status || !thread) {
        return;
    }

    var storageKey = widget.dataset.storageKey || "djopenkb.openkb-ai.v2";
    var endpoint = widget.dataset.endpoint || "";
    var pollIntervalMilliseconds = Math.max(500, Number(widget.dataset.pollIntervalMilliseconds || 2000));
    var maximumMessages = 24;
    var maximumMessageCharacters = 8000;
    var pollTimer = null;
    var pollInProgress = false;

    var messages = {
        ready: widget.dataset.msgReady || "Ready",
        thinking: widget.dataset.msgThinking || "OpenKB AI is thinking...",
        idle: widget.dataset.msgIdle || "Ask one question at a time.",
        questionRequired: widget.dataset.msgQuestionRequired || "Please type a question first.",
        genericError: widget.dataset.msgGenericError || "Something went wrong.",
        requestFailed: widget.dataset.msgRequestFailed || "Request failed.",
        recommended: widget.dataset.msgRecommended || "Recommended articles",
        untitled: widget.dataset.msgUntitled || "Untitled article",
        welcomeTitle: widget.dataset.msgWelcomeTitle || "Hi, I am OpenKB AI.",
        welcomeIntro: widget.dataset.msgWelcomeIntro || "I can help you find articles and answer questions from the wiki.",
        welcomeTipOne: widget.dataset.msgWelcomeTipOne || "Ask questions using general keywords or article topics.",
        welcomeTipTwo: widget.dataset.msgWelcomeTipTwo || "Ask for related articles when you want source links."
    };

    var state = { version: 2, open: false, draft: "", messages: [], pendingJobs: [] };

    function trimText(value, maximumLength) {
        if (typeof value !== "string") { return ""; }
        return value.slice(0, maximumLength);
    }

    function isUuid(value) {
        return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
    }

    function safeRelativeArticleUrl(value) {
        if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) { return ""; }
        return value;
    }

    function normaliseRelatedArticles(value) {
        if (!Array.isArray(value)) { return []; }
        return value.slice(0, 3).map(function (article) {
            if (!article || typeof article !== "object") { return null; }
            var url = safeRelativeArticleUrl(article.url);
            if (!url) { return null; }
            return {
                url: url,
                title: trimText(article.title, 400),
                visibility_label: trimText(article.visibility_label, 80),
                snippet: trimText(article.snippet, 700)
            };
        }).filter(Boolean);
    }

    function normaliseMessages(value) {
        if (!Array.isArray(value)) { return []; }
        return value.slice(-maximumMessages).map(function (message) {
            if (!message || typeof message !== "object") { return null; }
            if (message.sender !== "user" && message.sender !== "bot") { return null; }
            var text = trimText(message.text, maximumMessageCharacters);
            if (!text) { return null; }
            return {
                sender: message.sender,
                text: text,
                time: trimText(message.time, 32),
                jobId: isUuid(message.jobId) ? message.jobId : "",
                relatedArticles: normaliseRelatedArticles(message.relatedArticles)
            };
        }).filter(Boolean);
    }

    function normalisePendingJobs(value) {
        if (!Array.isArray(value)) { return []; }
        var seen = {};
        return value.slice(-3).map(function (job) {
            if (!job || typeof job !== "object" || !isUuid(job.id) || seen[job.id]) { return null; }
            seen[job.id] = true;
            return { id: job.id, createdAt: Number(job.createdAt) || Date.now() };
        }).filter(Boolean);
    }

    function readStoredState() {
        try {
            var stored = window.sessionStorage.getItem(storageKey);
            if (!stored) { return; }
            var parsed = JSON.parse(stored);
            if (!parsed || typeof parsed !== "object" || (parsed.version !== 1 && parsed.version !== 2)) { return; }
            state.open = Boolean(parsed.open);
            state.draft = trimText(parsed.draft, maximumMessageCharacters);
            state.messages = normaliseMessages(parsed.messages);
            state.pendingJobs = parsed.version === 2 ? normalisePendingJobs(parsed.pendingJobs) : [];
        } catch (error) {
            // Private-browser modes may deny session storage. The widget still works without persistence.
        }
    }

    function saveState() {
        try {
            window.sessionStorage.setItem(storageKey, JSON.stringify({
                version: 2,
                open: Boolean(state.open),
                draft: trimText(state.draft, maximumMessageCharacters),
                messages: normaliseMessages(state.messages),
                pendingJobs: normalisePendingJobs(state.pendingJobs)
            }));
        } catch (error) {
            // Do not interrupt a user question if browser storage is unavailable or full.
        }
    }

    function clearStoredState() {
        try { window.sessionStorage.removeItem(storageKey); } catch (error) {
            // The visible thread is cleared even when storage removal is unavailable.
        }
    }

    function getTime() {
        return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    function scrollThreadToBottom() { thread.scrollTop = thread.scrollHeight; }

    function createRelatedArticles(articles) {
        var wrapper = document.createElement("div");
        wrapper.className = "openkb-ai-related";

        var title = document.createElement("div");
        title.className = "openkb-ai-related-title";
        title.textContent = messages.recommended;
        wrapper.appendChild(title);

        articles.forEach(function (article) {
            var card = document.createElement("div");
            card.className = "openkb-ai-related-card";

            var link = document.createElement("a");
            link.href = article.url;
            link.textContent = article.title || messages.untitled;
            card.appendChild(link);

            if (article.visibility_label) {
                var badge = document.createElement("span");
                badge.className = "openkb-ai-related-meta";
                badge.textContent = article.visibility_label;
                card.appendChild(badge);
            }

            if (article.snippet) {
                var snippet = document.createElement("div");
                snippet.className = "openkb-ai-related-snippet";
                snippet.textContent = article.snippet;
                card.appendChild(snippet);
            }
            wrapper.appendChild(card);
        });
        return wrapper;
    }

    function createMessageRow(message) {
        var row = document.createElement("div");
        row.className = "openkb-ai-row " + message.sender;
        if (message.jobId) { row.dataset.jobId = message.jobId; }

        var bubble = document.createElement("div");
        bubble.className = "openkb-ai-bubble";

        var text = document.createElement("div");
        text.className = "openkb-ai-message-text";
        text.textContent = message.text;
        bubble.appendChild(text);

        if (message.relatedArticles && message.relatedArticles.length) {
            bubble.appendChild(createRelatedArticles(message.relatedArticles));
        }

        var time = document.createElement("span");
        time.className = "openkb-ai-time";
        time.textContent = message.time || getTime();
        bubble.appendChild(time);
        row.appendChild(bubble);
        return row;
    }

    function renderWelcome() {
        var row = document.createElement("div");
        row.className = "openkb-ai-row bot";
        var bubble = document.createElement("div");
        bubble.className = "openkb-ai-bubble";
        var content = document.createElement("div");
        content.className = "openkb-ai-message-text openkb-ai-welcome";

        var heading = document.createElement("div");
        heading.className = "openkb-ai-welcome-title";
        heading.textContent = messages.welcomeTitle;
        content.appendChild(heading);

        var intro = document.createElement("div");
        intro.className = "openkb-ai-welcome-intro";
        intro.textContent = messages.welcomeIntro;
        content.appendChild(intro);

        var list = document.createElement("ul");
        list.className = "openkb-ai-welcome-list";
        [messages.welcomeTipOne, messages.welcomeTipTwo].forEach(function (tip) {
            var item = document.createElement("li");
            item.textContent = tip;
            list.appendChild(item);
        });
        content.appendChild(list);
        bubble.appendChild(content);

        var time = document.createElement("span");
        time.className = "openkb-ai-time";
        time.textContent = messages.ready;
        bubble.appendChild(time);
        row.appendChild(bubble);
        thread.appendChild(row);
    }

    function createTypingRow(jobId) {
        var row = document.createElement("div");
        row.className = "openkb-ai-row bot openkb-ai-pending";
        row.dataset.jobId = jobId;
        var bubble = document.createElement("div");
        bubble.className = "openkb-ai-bubble";
        var dots = document.createElement("span");
        dots.className = "openkb-ai-typing";
        dots.textContent = "•••";
        bubble.appendChild(dots);
        row.appendChild(bubble);
        return row;
    }

    function renderThread() {
        thread.replaceChildren();
        if (!state.messages.length) {
            renderWelcome();
        } else {
            state.messages.forEach(function (message) {
                thread.appendChild(createMessageRow(message));
            });
        }
        state.pendingJobs.forEach(function (job) {
            thread.appendChild(createTypingRow(job.id));
        });
        scrollThreadToBottom();
    }

    function hasBotMessageForJob(jobId) {
        return state.messages.some(function (message) {
            return message.sender === "bot" && message.jobId === jobId;
        });
    }

    function appendMessage(sender, text, relatedArticles, jobId) {
        var message = {
            sender: sender === "user" ? "user" : "bot",
            text: trimText(text, maximumMessageCharacters),
            time: getTime(),
            jobId: isUuid(jobId) ? jobId : "",
            relatedArticles: normaliseRelatedArticles(relatedArticles)
        };
        if (!message.text) { return; }
        state.messages.push(message);
        state.messages = normaliseMessages(state.messages);
        thread.appendChild(createMessageRow(message));
        scrollThreadToBottom();
        saveState();
    }

    function hasPendingJobs() { return state.pendingJobs.length > 0; }

    function updateInputState() {
        var waiting = hasPendingJobs();
        questionInput.disabled = waiting;
        sendButton.disabled = waiting;
        status.textContent = waiting ? messages.thinking : messages.idle;
    }

    function addPendingJob(jobId) {
        if (!isUuid(jobId) || state.pendingJobs.some(function (job) { return job.id === jobId; })) { return; }
        state.pendingJobs.push({ id: jobId, createdAt: Date.now() });
        state.pendingJobs = normalisePendingJobs(state.pendingJobs);
        saveState();
        renderThread();
        updateInputState();
        startPolling();
    }

    function removePendingJob(jobId) {
        state.pendingJobs = state.pendingJobs.filter(function (job) { return job.id !== jobId; });
        saveState();
        renderThread();
        updateInputState();
        if (!hasPendingJobs()) { stopPolling(); }
    }

    function setChatOpen(open) {
        state.open = Boolean(open);
        chat.classList.toggle("open", state.open);
        chat.setAttribute("aria-hidden", state.open ? "false" : "true");
        saveState();
        if (state.open && !hasPendingJobs()) {
            window.setTimeout(function () { questionInput.focus(); }, 50);
        }
    }

    function jobStatusUrl(jobId) {
        return endpoint + "jobs/" + encodeURIComponent(jobId) + "/";
    }

    function jobCancelUrl(jobId) {
        return endpoint + "jobs/" + encodeURIComponent(jobId) + "/cancel/";
    }

    function cancelJobInBackground(jobId) {
        if (!endpoint || !isUuid(jobId)) { return; }
        fetch(jobCancelUrl(jobId), {
            method: "POST",
            headers: {
                "X-CSRFToken": csrfInput ? csrfInput.value : "",
                "X-Requested-With": "XMLHttpRequest"
            },
            credentials: "same-origin",
            keepalive: true
        }).catch(function () {
            // Clearing the visible chat must still work even if the request is interrupted.
        });
    }

    function clearConversation() {
        state.pendingJobs.forEach(function (job) { cancelJobInBackground(job.id); });
        state.messages = [];
        state.pendingJobs = [];
        state.draft = "";
        questionInput.value = "";
        clearStoredState();
        renderThread();
        updateInputState();
        stopPolling();
        if (state.open) {
            window.setTimeout(function () { questionInput.focus(); }, 50);
        }
    }

    function handleJobPayload(jobId, data) {
        var jobStatus = data && typeof data.status === "string" ? data.status : "";
        if (jobStatus === "queued" || jobStatus === "running") { return; }

        if (jobStatus === "completed") {
            if (!hasBotMessageForJob(jobId)) {
                var answer = typeof data.answer === "string" && data.answer ? data.answer : messages.genericError;
                var relatedArticles = data.show_related_articles ? data.related_articles : [];
                appendMessage("bot", answer, relatedArticles, jobId);
            }
            removePendingJob(jobId);
            return;
        }

        if (jobStatus === "failed" || jobStatus === "revoked") {
            if (!hasBotMessageForJob(jobId)) {
                appendMessage("bot", (typeof data.error === "string" && data.error) || messages.genericError, [], jobId);
            }
            removePendingJob(jobId);
            return;
        }

        if (jobStatus === "cancelled" || jobStatus === "expired") {
            removePendingJob(jobId);
            return;
        }

        if (!hasBotMessageForJob(jobId)) {
            appendMessage("bot", messages.genericError, [], jobId);
        }
        removePendingJob(jobId);
    }

    async function pollPendingJobs() {
        if (pollInProgress || !hasPendingJobs() || !endpoint) { return; }
        pollInProgress = true;
        var jobs = state.pendingJobs.slice();

        try {
            await Promise.all(jobs.map(async function (job) {
                try {
                    var response = await fetch(jobStatusUrl(job.id), {
                        method: "GET",
                        headers: { "X-Requested-With": "XMLHttpRequest" },
                        credentials: "same-origin"
                    });
                    var data = await response.json().catch(function () { return {}; });
                    if (!response.ok && (!data || !data.status)) {
                        data = { status: "expired" };
                    }
                    handleJobPayload(job.id, data || {});
                } catch (error) {
                    // Keep waiting after a transient network issue. The server job remains valid until TTL expiry.
                }
            }));
        } finally {
            pollInProgress = false;
            if (!hasPendingJobs()) { stopPolling(); }
        }
    }

    function startPolling() {
        if (!hasPendingJobs() || !endpoint) { return; }
        pollPendingJobs();
        if (!pollTimer) {
            pollTimer = window.setInterval(pollPendingJobs, pollIntervalMilliseconds);
        }
    }

    function stopPolling() {
        if (pollTimer) {
            window.clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    function applySavedState() {
        readStoredState();
        questionInput.value = state.draft;
        renderThread();
        setChatOpen(state.open);
        updateInputState();
        startPolling();
    }

    toggle.addEventListener("click", function () { setChatOpen(!state.open); });
    closeButton.addEventListener("click", function () { setChatOpen(false); });
    clearButton.addEventListener("click", clearConversation);
    questionInput.addEventListener("input", function () {
        state.draft = trimText(questionInput.value, maximumMessageCharacters);
        saveState();
    });

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        if (hasPendingJobs() || !endpoint) { return; }

        var question = questionInput.value.trim();
        if (!question) {
            appendMessage("bot", messages.questionRequired, []);
            return;
        }

        appendMessage("user", question, []);
        state.draft = "";
        questionInput.value = "";
        saveState();

        var formData = new FormData(form);
        formData.set("question", question);

        try {
            var response = await fetch(endpoint, {
                method: "POST",
                headers: {
                    "X-CSRFToken": csrfInput ? csrfInput.value : "",
                    "X-Requested-With": "XMLHttpRequest"
                },
                credentials: "same-origin",
                body: formData
            });
            var data = await response.json().catch(function () { return {}; });
            if (response.ok && data && isUuid(data.job_id)) {
                addPendingJob(data.job_id);
                return;
            }
            appendMessage("bot", (data && typeof data.error === "string" && data.error) || messages.genericError, []);
        } catch (error) {
            appendMessage("bot", messages.requestFailed, []);
        } finally {
            if (state.open && !hasPendingJobs()) { questionInput.focus(); }
        }
    });

    applySavedState();
}());
