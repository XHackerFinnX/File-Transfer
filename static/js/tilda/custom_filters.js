(() => {
    const closeAll = (except = null) => {
        document
            .querySelectorAll(".custom-filter.is-open")
            .forEach((filter) => {
                if (filter !== except) filter.classList.remove("is-open");
            });
    };

    const emitNativeEvents = (element) => {
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
    };

    const formatDateLabel = (value) => {
        if (!value) return "Выберите дату";
        const [year, month, day] = value.split("-");
        return `${day}.${month}.${year}`;
    };

    function buildSelect(filter, select) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "custom-filter__button";
        const panel = document.createElement("div");
        panel.className = "custom-filter__panel";
        const options = document.createElement("div");
        options.className = "custom-filter__options";
        panel.append(options);
        select.after(button, panel);

        const sync = () => {
            const selected = select.selectedOptions[0];
            button.textContent = selected?.textContent || "Все";
            options.replaceChildren(
                ...Array.from(select.options).map((option) => {
                    const optionButton = document.createElement("button");
                    optionButton.type = "button";
                    optionButton.className = "custom-filter__option";
                    optionButton.dataset.value = option.value;
                    optionButton.textContent = option.textContent;
                    if (option.value === select.value) {
                        optionButton.classList.add("is-selected");
                    }
                    return optionButton;
                }),
            );
        };

        button.addEventListener("click", () => {
            const willOpen = !filter.classList.contains("is-open");
            closeAll(filter);
            filter.classList.toggle("is-open", willOpen);
        });
        options.addEventListener("click", (event) => {
            const option = event.target.closest("[data-value]");
            if (!option) return;
            select.value = option.dataset.value;
            sync();
            closeAll();
            emitNativeEvents(select);
        });
        select.addEventListener("change", sync);
        document.addEventListener("tilda:filters-options-updated", sync);
        document.addEventListener("tilda:filters-reset", sync);
        sync();
    }

    function buildDate(filter, input) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "custom-filter__button";
        const panel = document.createElement("div");
        panel.className = "custom-filter__panel";
        const picker = document.createElement("div");
        picker.className = "custom-date-picker";
        picker.innerHTML = `
            <input type="date" data-custom-date-picker />
            <div class="custom-date-picker__actions">
                <button type="button" data-apply-date>Применить</button>
                <button type="button" data-clear-date>Очистить</button>
            </div>`;
        panel.append(picker);
        input.after(button, panel);
        const pickerInput = picker.querySelector("[data-custom-date-picker]");

        const sync = () => {
            button.textContent = formatDateLabel(input.value);
            pickerInput.value = input.value;
        };

        button.addEventListener("click", () => {
            const willOpen = !filter.classList.contains("is-open");
            closeAll(filter);
            filter.classList.toggle("is-open", willOpen);
            if (willOpen) pickerInput.focus();
        });
        picker
            .querySelector("[data-apply-date]")
            .addEventListener("click", () => {
                input.value = pickerInput.value;
                sync();
                closeAll();
                emitNativeEvents(input);
            });
        picker
            .querySelector("[data-clear-date]")
            .addEventListener("click", () => {
                input.value = "";
                sync();
                closeAll();
                emitNativeEvents(input);
            });
        pickerInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter")
                picker.querySelector("[data-apply-date]").click();
        });
        input.addEventListener("change", sync);
        document.addEventListener("tilda:filters-reset", sync);
        sync();
    }

    document
        .querySelectorAll("[data-custom-select-filter]")
        .forEach((filter) => {
            const select = filter.querySelector("[data-custom-select]");
            if (select) buildSelect(filter, select);
        });
    document.querySelectorAll("[data-custom-date-filter]").forEach((filter) => {
        const input = filter.querySelector("[data-custom-date]");
        if (input) buildDate(filter, input);
    });
    document.addEventListener("click", (event) => {
        if (!event.target.closest(".custom-filter")) closeAll();
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") closeAll();
    });
})();
