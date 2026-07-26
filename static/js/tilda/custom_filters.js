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
            const icons = {
                telegram: '<svg class="custom-filter__icon" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M23.1117 4.49449C23.4296 2.94472 21.9074 1.65683 20.4317 2.227L2.3425 9.21601C0.694517 9.85273 0.621087 12.1572 2.22518 12.8975L6.1645 14.7157L8.03849 21.2746C8.13583 21.6153 8.40618 21.8791 8.74917 21.968C9.09216 22.0568 9.45658 21.9576 9.70712 21.707L12.5938 18.8203L16.6375 21.8531C17.8113 22.7334 19.5019 22.0922 19.7967 20.6549L23.1117 4.49449ZM3.0633 11.0816L21.1525 4.0926L17.8375 20.2531L13.1 16.6999C12.7019 16.4013 12.1448 16.4409 11.7929 16.7928L10.5565 18.0292L10.928 15.9861L18.2071 8.70703C18.5614 8.35278 18.5988 7.79106 18.2947 7.39293C17.9906 6.99479 17.4389 6.88312 17.0039 7.13168L6.95124 12.876L3.0633 11.0816ZM8.17695 14.4791L8.78333 16.6015L9.01614 15.321C9.05253 15.1209 9.14908 14.9366 9.29291 14.7928L11.5128 12.573L8.17695 14.4791Z" fill="currentColor"/></svg>',
                instagram: '<svg class="custom-filter__icon" viewBox="0 0 512 512" fill="currentColor" aria-hidden="true"><path d="M349.33,69.33a93.62,93.62,0,0,1,93.34,93.34V349.33a93.62,93.62,0,0,1-93.34,93.34H162.67a93.62,93.62,0,0,1-93.34-93.34V162.67a93.62,93.62,0,0,1,93.34-93.34H349.33m0-37.33H162.67C90.8,32,32,90.8,32,162.67V349.33C32,421.2,90.8,480,162.67,480H349.33C421.2,480,480,421.2,480,349.33V162.67C480,90.8,421.2,32,349.33,32Z"/><path d="M377.33,162.67a28,28,0,1,1,28-28A27.94,27.94,0,0,1,377.33,162.67Z"/><path d="M256,181.33A74.67,74.67,0,1,1,181.33,256,74.75,74.75,0,0,1,256,181.33M256,144A112,112,0,1,0,368,256,112,112,0,0,0,256,144Z"/></svg>',
                tiktok: '<svg class="custom-filter__icon" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M11.9 3C11.9 2.72386 12.1239 2.5 12.4 2.5H15.2C15.4597 2.5 15.6762 2.69883 15.6982 2.9576C15.8456 4.68994 16.601 5.69493 17.4512 6.27309C18.3233 6.86619 19.3321 7.03338 19.9778 7.00464C20.2536 6.99236 20.4872 7.20604 20.4995 7.48191C20.5118 7.75778 20.2981 7.99137 20.0222 8.00365C19.2013 8.04019 17.97 7.83529 16.8888 7.09999C15.8711 6.40784 15.0166 5.26502 14.7549 3.5H12.9V13.9666C12.9 15.6646 12.2277 16.9506 11.2484 17.6391C10.267 18.3291 9.00524 18.3869 7.9957 17.6557C6.74437 16.7493 6.5284 15.3245 7.03027 14.2013C7.46605 13.226 8.44075 12.4775 9.7 12.4544V10.1748C8.78509 10.2343 7.61007 10.4676 6.61391 11.0866C5.4598 11.8038 4.5 13.0635 4.5 15.3374C4.5 16.8087 4.89793 17.8589 5.48366 18.6091C6.07288 19.3638 6.88196 19.8527 7.76297 20.1451C8.64578 20.438 9.58405 20.5275 10.3975 20.493C11.2249 20.4579 11.865 20.2973 12.169 20.139C13.4079 19.4936 14.1093 18.8565 14.5221 18.0477C14.9437 17.2218 15.1 16.15 15.1 14.5541V9.0708C15.1 8.89826 15.189 8.7379 15.3354 8.64658C15.4818 8.55525 15.6649 8.54586 15.8199 8.62173C16.4456 8.9281 18.1979 9.59709 20.0407 9.74745C20.3159 9.76991 20.5208 10.0112 20.4983 10.2865C20.4759 10.5617 20.2346 10.7666 19.9593 10.7441C18.4542 10.6213 17.0188 10.185 16.1 9.82926V14.5541C16.1 16.1868 15.9463 17.4572 15.4128 18.5023C14.8706 19.5646 13.972 20.3273 12.631 21.0259C12.1423 21.2804 11.3361 21.4541 10.4399 21.4921C9.52965 21.5307 8.467 21.4323 7.44799 21.0942C6.42717 20.7554 5.43351 20.1698 4.69545 19.2245C3.95389 18.2748 3.5 16.9995 3.5 15.3374C3.5 12.7156 4.64021 11.1357 6.08611 10.2373C7.49667 9.36075 9.15221 9.1583 10.2 9.1583C10.4761 9.1583 10.7 9.38216 10.7 9.6583V12.9874C10.7 13.1331 10.6365 13.2714 10.5262 13.3664C10.4158 13.4614 10.2695 13.5036 10.1255 13.4819C9.06028 13.3214 8.27059 13.8767 7.94327 14.6092C7.6178 15.3376 7.74632 16.2403 8.5823 16.8458C9.21629 17.305 10.0101 17.2873 10.6732 16.8211C11.3386 16.3532 11.9 15.4019 11.9 13.9666V3Z" fill="currentColor"/></svg>',
                vk: '<svg class="custom-filter__icon" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M23.405 16.865C22.8611 15.7695 22.1444 14.7688 21.2825 13.9013C20.9892 13.5603 20.6453 13.2238 20.3768 12.9612L20.3393 12.9245C20.2008 12.7889 20.0864 12.6764 19.9928 12.5795C21.1713 10.9407 22.18 9.18595 23.003 7.34222L23.0362 7.26783L23.0595 7.18976C23.1676 6.82687 23.2922 6.1368 22.8515 5.51317C22.396 4.86859 21.6666 4.75234 21.1782 4.75234H18.9311C18.4627 4.73087 17.9988 4.85751 17.6058 5.11498C17.2098 5.37439 16.9069 5.75278 16.7402 6.1951C16.2563 7.34779 15.6508 8.4442 14.9347 9.46598V6.83269C14.9347 6.4923 14.9027 5.92289 14.5382 5.44229C14.1018 4.86685 13.4707 4.75234 13.0326 4.75234H9.46708C9.00771 4.74172 8.56094 4.90597 8.2176 5.21259C7.866 5.52659 7.65052 5.96521 7.61687 6.43543L7.61369 6.47997V6.52463C7.61369 7.01011 7.80606 7.36822 7.95975 7.59344C8.02856 7.69427 8.10216 7.78606 8.14865 7.84403L8.15938 7.85741C8.20895 7.91923 8.24204 7.96049 8.27525 8.00566C8.3626 8.12448 8.48824 8.30768 8.52379 8.78174V10.2547C7.9091 9.24423 7.26066 7.89957 6.77276 6.46344L6.76527 6.4414L6.75697 6.41965C6.63532 6.10103 6.4402 5.63743 6.04941 5.28266C5.59288 4.86821 5.0529 4.75234 4.56182 4.75234H2.28187C1.78506 4.75234 1.18613 4.86857 0.739237 5.33999C0.299773 5.80358 0.25 6.35907 0.25 6.65442V6.78755L0.278039 6.91769C0.909544 9.84881 2.21076 12.5937 4.07946 14.9377C4.92668 16.2737 6.07468 17.3936 7.43213 18.2075C8.81124 19.0345 10.3671 19.5219 11.9715 19.6297L12.0133 19.6325H12.0553C12.7811 19.6325 13.5378 19.5699 14.1068 19.1907C14.8744 18.6792 14.9347 17.8936 14.9347 17.5021V16.3642C15.1317 16.5234 15.3761 16.7378 15.6753 17.0259C16.037 17.3879 16.325 17.7016 16.572 17.9754L16.7038 18.122L16.7046 18.1228C16.8964 18.3364 17.0852 18.5467 17.2571 18.7195C17.4732 18.9367 17.7396 19.1761 18.0745 19.3529C18.4371 19.5444 18.8177 19.631 19.222 19.631H21.5035C21.9841 19.631 22.6735 19.5173 23.1582 18.9554C23.6864 18.343 23.6461 17.5924 23.48 17.053L23.4501 16.956L23.405 16.865Z" fill="currentColor"/></svg>',
                max: '<svg class="custom-filter__icon" viewBox="0 0 720 720" aria-hidden="true"><path fill="currentColor" d="M350.4,9.6C141.8,20.5,4.1,184.1,12.8,390.4c3.8,90.3,40.1,168,48.7,253.7,2.2,22.2-4.2,49.6,21.4,59.3,31.5,11.9,79.8-8.1,106.2-26.4,9-6.1,17.6-13.2,24.2-22,27.3,18.1,53.2,35.6,85.7,43.4,143.1,34.3,299.9-44.2,369.6-170.3C799.6,291.2,622.5-4.6,350.4,9.6h0ZM269.4,504c-11.3,8.8-22.2,20.8-34.7,27.7-18.1,9.7-23.7-.4-30.5-16.4-21.4-50.9-24-137.6-11.5-190.9,16.8-72.5,72.9-136.3,150-143.1,78-6.9,150.4,32.7,183.1,104.2,72.4,159.1-112.9,316.2-256.4,218.6h0Z"/></svg>',
                youtube: '<svg class="custom-filter__icon" viewBox="-2 -5 24 24" fill="currentColor" aria-hidden="true"><path d="M15.812.017H4.145C1.855.017 0 1.852 0 4.116v5.768c0 2.264 1.856 4.1 4.145 4.1h11.667c2.29 0 4.145-1.836 4.145-4.1V4.116c0-2.264-1.856-4.1-4.145-4.1zM13.009 7.28L7.552 9.855a.219.219 0 0 1-.314-.196V4.35c0-.161.173-.266.318-.193l5.458 2.735a.216.216 0 0 1-.005.389z"/></svg>',
                minus: '<svg class="custom-filter__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"></path></svg>',
            };
            return icons[name] || "";
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
