/*
 * Login MFA countdown.
 *
 * The visible timer is only a user-facing aid. Django keeps the authoritative
 * password-to-MFA deadline in the server-side session and checks it again on
 * every MFA setup/verification POST, so refreshing the page or disabling
 * JavaScript cannot extend or bypass the timeout.
 */
(function () {
    "use strict";

    var container = document.getElementById("mfa-login-countdown");
    var valueElement = document.getElementById("mfa-login-countdown-value");
    var timeoutForm = document.getElementById("mfa-timeout-form");

    if (!container || !valueElement || !timeoutForm) {
        return;
    }

    var initialSeconds = Number.parseInt(container.dataset.remainingSeconds || "0", 10);
    if (!Number.isFinite(initialSeconds) || initialSeconds < 0) {
        return;
    }

    var submitted = false;
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
        var minutes = Math.floor(totalSeconds / 60);
        var seconds = totalSeconds % 60;
        return String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
    }

    function updateCountdown() {
        var remaining = Math.max(0, initialSeconds - elapsedSeconds());
        valueElement.textContent = formatSeconds(remaining);

        if (remaining === 0 && !submitted) {
            submitted = true;
            window.clearInterval(timerId);
            timeoutForm.submit();
        }
    }

    var timerId = window.setInterval(updateCountdown, 250);
    updateCountdown();
}());
