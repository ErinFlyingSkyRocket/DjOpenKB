/*
 * Authentication lockout countdowns.
 *
 * Django/Redis remains authoritative for every cooldown. This script only
 * disables the controls already marked by the server, updates the visible
 * countdown, and reloads once the displayed cooldown reaches zero so Django
 * can re-check the lockout before enabling the form again.
 */
(function () {
    "use strict";

    var containers = document.querySelectorAll(".auth-lockout-countdown");
    var marker = "__DJOPENKB_AUTH_LOCKOUT_COUNTDOWN__";
    var reloadScheduled = false;

    if (!containers.length) {
        return;
    }

    function nowMilliseconds() {
        if (window.performance && typeof window.performance.now === "function") {
            return window.performance.now();
        }
        return Date.now();
    }

    function formatSeconds(totalSeconds) {
        var minutes = Math.floor(totalSeconds / 60);
        var seconds = totalSeconds % 60;

        if (minutes > 0) {
            return String(minutes) + "m " + String(seconds) + "s";
        }
        return String(seconds) + "s";
    }

    function disableProtectedControls(container) {
        var scope = container.closest("[data-auth-lockout-scope]") || document;
        var group = container.getAttribute("data-lockout-group") || "default";
        var controls = scope.querySelectorAll("[data-auth-lockout-control]");

        Array.prototype.forEach.call(controls, function (control) {
            var groups = (control.getAttribute("data-auth-lockout-control") || "")
                .split(/\s+/)
                .filter(Boolean);
            if (groups.indexOf(group) === -1) {
                return;
            }
            control.disabled = true;
            control.setAttribute("aria-disabled", "true");
        });
    }

    function scheduleReload() {
        if (reloadScheduled) {
            return;
        }
        reloadScheduled = true;
        window.setTimeout(function () {
            // Assigning the current URL performs a clean GET even when the
            // cooldown page was rendered from an invalid POST response. This
            // avoids browser form-resubmission prompts and duplicate failures.
            window.location.replace(window.location.pathname + window.location.search);
        }, 250);
    }

    Array.prototype.forEach.call(containers, function (container) {
        var initialSeconds = parseInt(
            container.getAttribute("data-remaining-seconds") || "0",
            10
        );
        var messageTemplate = container.getAttribute("data-message-template") || marker;
        var startedAt = nowMilliseconds();
        var timerId = null;

        if (!isFinite(initialSeconds) || initialSeconds <= 0) {
            return;
        }

        disableProtectedControls(container);

        function updateCountdown() {
            var elapsed = Math.floor((nowMilliseconds() - startedAt) / 1000);
            var remaining = Math.max(0, initialSeconds - elapsed);
            container.textContent = messageTemplate.replace(marker, formatSeconds(remaining));

            if (remaining === 0) {
                if (timerId !== null) {
                    window.clearInterval(timerId);
                }
                scheduleReload();
            }
        }

        timerId = window.setInterval(updateCountdown, 250);
        updateCountdown();
    });
}());
