/* Strict-CSP interaction helpers.
 * Inline onclick/onsubmit handlers are intentionally not used. */
(function () {
    "use strict";

    function confirmationMessage(element) {
        return element && element.dataset ? (element.dataset.confirmMessage || "") : "";
    }

    document.addEventListener("submit", function (event) {
        var form = event.target.closest("form[data-confirm-message]");
        if (!form) {
            return;
        }
        var message = confirmationMessage(form);
        if (message && !window.confirm(message)) {
            event.preventDefault();
        }
    });

    document.addEventListener("click", function (event) {
        var confirmButton = event.target.closest("[data-confirm-on-click][data-confirm-message]");
        if (confirmButton) {
            var message = confirmationMessage(confirmButton);
            if (message && !window.confirm(message)) {
                event.preventDefault();
                event.stopImmediatePropagation();
                return;
            }
        }

        var actionButton = event.target.closest("[data-checkboxes-action][data-checkboxes-target]");
        if (!actionButton) {
            return;
        }

        var boxes = document.querySelectorAll(actionButton.dataset.checkboxesTarget);
        var shouldSelect = actionButton.dataset.checkboxesAction === "select";
        boxes.forEach(function (box) {
            box.checked = shouldSelect;
        });

        var selectAllId = actionButton.dataset.checkboxesSelectAll;
        if (selectAllId) {
            var selectAll = document.getElementById(selectAllId);
            if (selectAll) {
                selectAll.checked = shouldSelect;
            }
        }
    });

    document.addEventListener("change", function (event) {
        var toggle = event.target.closest("[data-checkboxes-toggle]");
        if (!toggle) {
            return;
        }
        document.querySelectorAll(toggle.dataset.checkboxesToggle).forEach(function (box) {
            box.checked = toggle.checked;
        });
    });
}());
