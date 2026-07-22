(() => {
    const closeAll = (except = null) => {
        document
            .querySelectorAll(".custom-filter.is-open")
            .forEach((filter) => {
                if (filter !== except) {
                    filter.classList.remove("is-open");
                    filter
                        .querySelector(".custom-filter__button")
                        ?.setAttribute("aria-expanded", "false");
                }
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

    const setOpen = (filter, button, open) => {
        filter.classList.toggle("is-open", open);
        button.setAttribute("aria-expanded", String(open));
    };

    function buildSelect(filter, select) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "custom-filter__button";
        button.setAttribute("aria-haspopup", "listbox");
        button.setAttribute("aria-expanded", "false");
        const panel = document.createElement("div");
        panel.className = "custom-filter__panel";
        const options = document.createElement("div");
        options.className = "custom-filter__options";
        options.setAttribute("role", "listbox");
        panel.append(options);
        select.after(button, panel);

        const iconSvg = (name) => {
            if (!name) return "";
            const paths = {
                camera: '<rect x="4" y="5" width="16" height="14" rx="4"></rect><circle cx="12" cy="12" r="3.5"></circle><path d="M16.5 8.5h.01"></path>',
                music: '<path d="M14 4v10.5a3.5 3.5 0 1 1-2-3.16V6l6-1.5v8a3.5 3.5 0 1 1-2-3.16V4.5L14 5"></path>',
                "paper-plane":
                    '<path d="M21 3 10 14"></path><path d="m21 3-7 18-4-7-7-4 18-7Z"></path>',
                vk: '<path d="M4 8c.12 5.35 2.78 8.56 7.44 8.56h.27v-3.06c1.7.17 2.98 1.42 3.49 3.06H18c-.66-2.45-2.39-3.78-3.47-4.29 1.08-.63 2.6-2.16 2.96-4.27h-2.54c-.47 1.7-1.87 3.23-3.24 3.38V8H9.17v5.92C7.74 13.56 5.93 11.9 5.85 8H4Z"></path>',
                chat: '<path d="M21 12a8 8 0 0 1-8 8H7l-4 3 1.4-5.1A8 8 0 1 1 21 12Z"></path>',
                minus: '<path d="M5 12h14"></path>',
            };
            return `<svg class="custom-filter__icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.chat}</svg>`;
        };

        const optionLabelHtml = (option) =>
            `${iconSvg(option.dataset.icon || "")}<span>${option.textContent || "Все"}</span>`;

        const sync = () => {
            const selected = select.selectedOptions[0];
            button.innerHTML = selected
                ? optionLabelHtml(selected)
                : "<span>Все</span>";
            options.replaceChildren(
                ...Array.from(select.options).map((option) => {
                    const optionButton = document.createElement("button");
                    optionButton.type = "button";
                    optionButton.className = "custom-filter__option";
                    optionButton.dataset.value = option.value;
                    optionButton.innerHTML = optionLabelHtml(option);
                    optionButton.setAttribute("role", "option");
                    const isSelected = option.value === select.value;
                    optionButton.setAttribute(
                        "aria-selected",
                        String(isSelected),
                    );
                    if (isSelected) {
                        optionButton.classList.add("is-selected");
                    }
                    return optionButton;
                }),
            );
        };

        button.addEventListener("click", () => {
            const willOpen = !filter.classList.contains("is-open");
            closeAll(filter);
            setOpen(filter, button, willOpen);
        });
        options.addEventListener("click", (event) => {
            const option = event.target.closest("[data-value]");
            if (!option) return;
            select.value = option.dataset.value;
            sync();
            closeAll();
            button.focus();
            emitNativeEvents(select);
        });
        select.addEventListener("change", sync);
        document.addEventListener("tilda:filters-options-updated", sync);
        document.addEventListener("tilda:filters-reset", sync);
        sync();
    }

    const MONTH_NAMES = [
        "Январь",
        "Февраль",
        "Март",
        "Апрель",
        "Май",
        "Июнь",
        "Июль",
        "Август",
        "Сентябрь",
        "Октябрь",
        "Ноябрь",
        "Декабрь",
    ];
    const WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

    // Локальный парсинг ISO-даты (без сдвига часового пояса, в отличие
    // от new Date("YYYY-MM-DD"), которая интерпретируется как UTC).
    function parseIsoDateLocal(value) {
        if (!value) return null;
        const [year, month, day] = value.split("-").map(Number);
        if (!year || !month || !day) return null;
        const date = new Date(year, month - 1, day);
        return Number.isNaN(date.getTime()) ? null : date;
    }

    function toIsoDate(year, month, day) {
        return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    }

    function buildDate(filter, input) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "custom-filter__button";
        button.setAttribute("aria-haspopup", "dialog");
        button.setAttribute("aria-expanded", "false");

        const panel = document.createElement("div");
        panel.className = "custom-filter__panel";

        const picker = document.createElement("div");
        picker.className = "custom-date-picker";
        picker.innerHTML = `
            <div class="custom-calendar">
                <div class="custom-calendar__header">
                    <button type="button" class="custom-calendar__nav" data-prev-month aria-label="Предыдущий месяц">&#8249;</button>
                    <span class="custom-calendar__title" data-calendar-title></span>
                    <button type="button" class="custom-calendar__nav" data-next-month aria-label="Следующий месяц">&#8250;</button>
                </div>
                <div class="custom-calendar__weekdays">
                    ${WEEKDAY_LABELS.map((label) => `<span>${label}</span>`).join("")}
                </div>
                <div class="custom-calendar__days" data-calendar-days role="grid"></div>
            </div>
            <div class="custom-date-picker__actions">
                <button type="button" data-apply-date>Применить</button>
                <button type="button" data-clear-date>Очистить</button>
            </div>`;
        panel.append(picker);
        input.after(button, panel);

        const titleEl = picker.querySelector("[data-calendar-title]");
        const daysEl = picker.querySelector("[data-calendar-days]");

        // pendingValue — дата, выбранная кликом по календарю, но ещё не
        // подтверждённая кнопкой «Применить». Это отдельное состояние от
        // input.value, чтобы можно было листать месяцы и передумать,
        // не трогая уже применённый фильтр до явного подтверждения.
        let pendingValue = input.value || "";
        let viewYear;
        let viewMonth;

        function setViewFromValue(value) {
            const ref = parseIsoDateLocal(value) || new Date();
            viewYear = ref.getFullYear();
            viewMonth = ref.getMonth();
        }

        function renderCalendar() {
            titleEl.textContent = `${MONTH_NAMES[viewMonth]} ${viewYear}`;

            const firstOfMonth = new Date(viewYear, viewMonth, 1);
            // Понедельник = 0 ... Воскресенье = 6
            const startOffset = (firstOfMonth.getDay() + 6) % 7;
            const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
            const daysInPrevMonth = new Date(viewYear, viewMonth, 0).getDate();
            const today = new Date();
            const todayValue = toIsoDate(
                today.getFullYear(),
                today.getMonth(),
                today.getDate(),
            );

            const cells = [];
            for (let i = 0; i < startOffset; i += 1) {
                cells.push({
                    day: daysInPrevMonth - startOffset + i + 1,
                    muted: true,
                });
            }
            for (let day = 1; day <= daysInMonth; day += 1) {
                cells.push({
                    day,
                    muted: false,
                    value: toIsoDate(viewYear, viewMonth, day),
                });
            }
            let trailing = 1;
            while (cells.length % 7 !== 0) {
                cells.push({ day: trailing, muted: true });
                trailing += 1;
            }

            daysEl.innerHTML = cells
                .map((cell) => {
                    if (cell.muted) {
                        return `<span class="custom-calendar__day is-muted" aria-hidden="true">${cell.day}</span>`;
                    }
                    const isSelected = cell.value === pendingValue;
                    const isToday = cell.value === todayValue;
                    const classes = ["custom-calendar__day"];
                    if (isSelected) classes.push("is-selected");
                    if (isToday) classes.push("is-today");
                    return `<button type="button" class="${classes.join(" ")}" role="gridcell" aria-selected="${isSelected}" data-day-value="${cell.value}">${cell.day}</button>`;
                })
                .join("");
        }

        function applyDateValue(value) {
            input.value = value;
            pendingValue = value;
            sync();
            closeAll();
            button.focus();
            // Диспатчим события именно на оригинальном скрытом input —
            // на него подписан рендер фильтрации в form.js.
            emitNativeEvents(input);
        }

        daysEl.addEventListener("click", (event) => {
            const dayButton = event.target.closest("[data-day-value]");
            if (!dayButton) return;
            // Выбор дня сразу должен отображаться в фильтре и запускать
            // фильтрацию. Кнопка «Применить» оставлена для пользователей,
            // которые меняют дату клавиатурой или ожидают явного действия.
            applyDateValue(dayButton.dataset.dayValue);
        });

        picker
            .querySelector("[data-prev-month]")
            .addEventListener("click", () => {
                viewMonth -= 1;
                if (viewMonth < 0) {
                    viewMonth = 11;
                    viewYear -= 1;
                }
                renderCalendar();
            });
        picker
            .querySelector("[data-next-month]")
            .addEventListener("click", () => {
                viewMonth += 1;
                if (viewMonth > 11) {
                    viewMonth = 0;
                    viewYear += 1;
                }
                renderCalendar();
            });

        // Синхронизация подписи кнопки и состояния календаря с
        // фактическим значением скрытого input (источник истины).
        const sync = () => {
            button.textContent = formatDateLabel(input.value);
            pendingValue = input.value || "";
            setViewFromValue(pendingValue);
            renderCalendar();
        };

        button.addEventListener("click", () => {
            const willOpen = !filter.classList.contains("is-open");
            closeAll(filter);
            setOpen(filter, button, willOpen);
            if (willOpen) {
                // При каждом открытии откатываем черновой выбор к уже
                // применённому значению, чтобы случайные клики по дням
                // без нажатия «Применить» не терялись и не путали пользователя.
                pendingValue = input.value || "";
                setViewFromValue(pendingValue);
                renderCalendar();
            }
        });

        picker
            .querySelector("[data-apply-date]")
            .addEventListener("click", () => {
                applyDateValue(pendingValue);
            });
        picker
            .querySelector("[data-clear-date]")
            .addEventListener("click", () => {
                applyDateValue("");
            });

        input.addEventListener("input", sync);
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
