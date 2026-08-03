/*
 * Login MFA countdown.
 *
 * Django stores and enforces the authoritative expiry time in the server-side
 * session. This script only updates the visible countdown and submits a normal
 * CSRF-protected POST to clear the pending login when the display reaches zero.
 */
(function () {
    "use strict";

    var container = document.getElementById("mfa-login-countdown");
    var valueElement = document.getElementById("mfa-login-countdown-value");

    if (!container || !valueElement) {
        return;
    }

    var initialSeconds = parseInt(container.getAttribute("data-remaining-seconds") || "0", 10);
    var cancelUrl = container.getAttribute("data-cancel-url") || "";

    if (!isFinite(initialSeconds) || initialSeconds < 0) {
        return;
    }

    var submitted = false;
    var timerId = null;
    var monotonicStart = window.performance && typeof window.performance.now === "function"
        ? window.performance.now()
        : Date.now();

    function elapsedSeconds() {
        var now = window.performance && typeof window.performance.now === "function"
            ? window.performance.now()
            : Date.now();
        return Math.floor((now - monotonicStart) / 1000);
    }

    function formatSeconds(totalSeconds) {
        return String(totalSeconds) + "s";
    }

    function submitTimeout() {
        if (submitted) {
            return;
        }
        submitted = true;

        if (timerId !== null) {
            window.clearInterval(timerId);
        }

        var csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
        var csrfToken = csrfInput ? csrfInput.value : "";

        if (!cancelUrl || !csrfToken) {
            // A reload still reaches Django, which independently rejects an
            // expired pending-MFA session and redirects back to password login.
            window.location.reload();
            return;
        }

        var timeoutForm = document.createElement("form");
        timeoutForm.method = "post";
        timeoutForm.action = cancelUrl;
        timeoutForm.style.display = "none";

        var csrfField = document.createElement("input");
        csrfField.type = "hidden";
        csrfField.name = "csrfmiddlewaretoken";
        csrfField.value = csrfToken;
        timeoutForm.appendChild(csrfField);

        var reasonField = document.createElement("input");
        reasonField.type = "hidden";
        reasonField.name = "reason";
        reasonField.value = "timeout";
        timeoutForm.appendChild(reasonField);

        document.body.appendChild(timeoutForm);
        timeoutForm.submit();
    }

    function updateCountdown() {
        var remaining = Math.max(0, initialSeconds - elapsedSeconds());
        valueElement.textContent = formatSeconds(remaining);

        if (remaining === 0) {
            submitTimeout();
        }
    }

    timerId = window.setInterval(updateCountdown, 250);
    updateCountdown();
}());
