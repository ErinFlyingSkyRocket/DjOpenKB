/*
 * Django Admin step-up MFA countdown.
 *
 * Django stores and enforces the authoritative fixed expiry time in the
 * server-side session. This script only updates the visible seconds and sends
 * a normal CSRF-protected timeout POST when the display reaches zero. Django
 * then keeps the administrator signed in and pauses until an explicit retry.
 */
(function () {
    "use strict";

    var container = document.getElementById("admin-mfa-countdown");
    var valueElement = document.getElementById("admin-mfa-countdown-value");

    if (!container || !valueElement) {
        return;
    }

    var initialSeconds = parseInt(
        container.getAttribute("data-remaining-seconds") || "0",
        10
    );
    var timeoutUrl = container.getAttribute("data-timeout-url") || "";
    var nextUrl = container.getAttribute("data-next-url") || "";

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

        if (!timeoutUrl || !csrfToken) {
            // A reload still reaches Django, which independently checks the
            // expired server-side deadline and starts a fresh window.
            window.location.reload();
            return;
        }

        var timeoutForm = document.createElement("form");
        timeoutForm.method = "post";
        timeoutForm.action = timeoutUrl;
        timeoutForm.style.display = "none";

        var csrfField = document.createElement("input");
        csrfField.type = "hidden";
        csrfField.name = "csrfmiddlewaretoken";
        csrfField.value = csrfToken;
        timeoutForm.appendChild(csrfField);

        var actionField = document.createElement("input");
        actionField.type = "hidden";
        actionField.name = "action";
        actionField.value = "timeout";
        timeoutForm.appendChild(actionField);

        if (nextUrl) {
            var nextField = document.createElement("input");
            nextField.type = "hidden";
            nextField.name = "next";
            nextField.value = nextUrl;
            timeoutForm.appendChild(nextField);
        }

        document.body.appendChild(timeoutForm);
        timeoutForm.submit();
    }

    function updateCountdown() {
        var remaining = Math.max(0, initialSeconds - elapsedSeconds());
        valueElement.textContent = String(remaining) + "s";

        if (remaining === 0) {
            submitTimeout();
        }
    }

    timerId = window.setInterval(updateCountdown, 250);
    updateCountdown();
}());
