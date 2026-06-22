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

    var storageKey = widget.dataset.storageKey || "djopenkb.openkb-ai.v1";
    var endpoint = widget.dataset.endpoint || "";
    var maximumMessages = 24;
    var maximumMessageCharacters = 8000;
    var isThinking = false;

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

    var state = { version: 1, open: false, draft: "", messages: [] };

    function trimText(value, maximumLength) {
        if (typeof value !== "string") { return ""; }
        return value.slice(0, maximumLength);
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
                relatedArticles: normaliseRelatedArticles(message.relatedArticles)
            };
        }).filter(Boolean);
    }

    function readStoredState() {
        try {
            var stored = window.sessionStorage.getItem(storageKey);
            if (!stored) { return; }
            var parsed = JSON.parse(stored);
            if (!parsed || parsed.version !== 1 || typeof parsed !== "object") { return; }
            state.open = Boolean(parsed.open);
            state.draft = trimText(parsed.draft, maximumMessageCharacters);
            state.messages = normaliseMessages(parsed.messages);
        } catch (error) {
            // Private-browser modes may deny session storage. The widget still works without persistence.
        }
    }

    function saveState() {
        try {
            window.sessionStorage.setItem(storageKey, JSON.stringify({
                version: 1,
                open: Boolean(state.open),
                draft: trimText(state.draft, maximumMessageCharacters),
                messages: normaliseMessages(state.messages)
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

    function renderThread() {
        thread.replaceChildren();
        if (!state.messages.length) {
            renderWelcome();
        } else {
            state.messages.forEach(function (message) {
                thread.appendChild(createMessageRow(message));
            });
        }
        scrollThreadToBottom();
    }

    function appendMessage(sender, text, relatedArticles) {
        var message = {
            sender: sender === "user" ? "user" : "bot",
            text: trimText(text, maximumMessageCharacters),
            time: getTime(),
            relatedArticles: normaliseRelatedArticles(relatedArticles)
        };
        if (!message.text) { return; }
        state.messages.push(message);
        state.messages = normaliseMessages(state.messages);
        thread.appendChild(createMessageRow(message));
        scrollThreadToBottom();
        saveState();
    }

    function createTypingRow() {
        var row = document.createElement("div");
        row.className = "openkb-ai-row bot";
        var bubble = document.createElement("div");
        bubble.className = "openkb-ai-bubble";
        var dots = document.createElement("span");
        dots.className = "openkb-ai-typing";
        dots.textContent = "•••";
        bubble.appendChild(dots);
        row.appendChild(bubble);
        thread.appendChild(row);
        scrollThreadToBottom();
        return row;
    }

    function setChatOpen(open) {
        state.open = Boolean(open);
        chat.classList.toggle("open", state.open);
        chat.setAttribute("aria-hidden", state.open ? "false" : "true");
        saveState();
        if (state.open && !isThinking) {
            window.setTimeout(function () { questionInput.focus(); }, 50);
        }
    }

    function setThinking(thinking) {
        isThinking = Boolean(thinking);
        questionInput.disabled = isThinking;
        sendButton.disabled = isThinking;
        status.textContent = isThinking ? messages.thinking : messages.idle;
    }

    function clearConversation() {
        state.messages = [];
        state.draft = "";
        questionInput.value = "";
        clearStoredState();
        renderThread();
        if (state.open) {
            window.setTimeout(function () { questionInput.focus(); }, 50);
        }
    }

    function applySavedState() {
        readStoredState();
        questionInput.value = state.draft;
        renderThread();
        setChatOpen(state.open);
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
        if (isThinking || !endpoint) { return; }

        var question = questionInput.value.trim();
        if (!question) {
            appendMessage("bot", messages.questionRequired, []);
            return;
        }

        appendMessage("user", question, []);
        state.draft = "";
        questionInput.value = "";
        saveState();
        setThinking(true);
        var typingRow = createTypingRow();
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
            var data = await response.json();
            var answer = (data && typeof data.answer === "string" && data.answer) ||
                (data && typeof data.error === "string" && data.error) || messages.genericError;
            var relatedArticles = data && data.show_related_articles ? data.related_articles : [];
            typingRow.remove();
            appendMessage("bot", answer, relatedArticles);
        } catch (error) {
            typingRow.remove();
            appendMessage("bot", messages.requestFailed, []);
        } finally {
            setThinking(false);
            if (state.open) { questionInput.focus(); }
        }
    });

    applySavedState();
}());
