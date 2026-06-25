(() => {
    const root = document.querySelector("[data-site-name]");
    if (!root) return;

    const siteName = root.dataset.siteName;
    const accessParams = new URLSearchParams(window.location.search);
    const secret =
        accessParams.get("secret") ||
        accessParams.get("token") ||
        accessParams.get("key") ||
        "";
    const list = document.querySelector("[data-submissions]");
    const status = document.querySelector("[data-status]");
    const search = document.querySelector("[data-search]");
    const refresh = document.querySelector("[data-refresh]");
    const dateFrom = document.querySelector("[data-date-from]");
    const dateTo = document.querySelector("[data-date-to]");
    const deliveryFilter = document.querySelector("[data-delivery-filter]");
    const sizeFilter = document.querySelector("[data-size-filter]");
    const colorFilter = document.querySelector("[data-color-filter]");
    const visibleCount = document.querySelector("[data-visible-count]");
    const daysTable = document.querySelector("[data-days-table]");
    const sizesTable = document.querySelector("[data-sizes-table]");
    const colorsTable = document.querySelector("[data-colors-table]");
    const pickupTable = document.querySelector("[data-pickup-table]");
    const exportCsv = document.querySelector("[data-export-csv]");
    const exportJson = document.querySelector("[data-export-json]");
    const summaryEls = {
        orders: document.querySelector("[data-total-orders]"),
        sum: document.querySelector("[data-total-sum]"),
        items: document.querySelector("[data-total-items]"),
        delivery: document.querySelector("[data-delivery-sum]"),
        buyers: document.querySelector("[data-unique-buyers]"),
        last: document.querySelector("[data-last]"),
    };

    let submissions = [];
    let orderRows = [];
    let visibleRows = [];

    const colorRules = [
        ["white", "Белые", /\bбел\w*\b/i],
        ["black", "Чёрные", /\bч[её]рн\w*\b/i],
        ["blue", "Синие", /\bсин\w*\b/i],
        ["red", "Красные", /\bкрас\w*\b/i],
        ["green", "Зелёные", /\bзел\w*\b/i],
        ["gray", "Серые", /\bсер\w*\b/i],
        ["pink", "Розовый", /\bроз\w*\b/i],
        ["cyan", "Голубой", /\bголу\w*\b/i],
    ];

    const deliveryPatterns = [
        /Доставка в ПВЗ СДЭК:\s*([\d.,]+)/i,
        /Доставка СДЭК по России:\s*([\d.,]+)/i,
        /Международная доставка:\s*([\d.,]+)/i,
        /International Delivery:\s*([\d.,]+)/i,
        /Доставка[^\d]*([\d.,]+)/i,
    ];

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function parseMoney(value) {
        if (value === null || value === undefined || value === "") return 0;
        const normalized = String(value)
            .replace(/\s/g, "")
            .replace(",", ".")
            .replace(/[^\d.-]/g, "");
        const parsed = Number.parseFloat(normalized);
        return Number.isFinite(parsed) ? parsed : 0;
    }

    function fmtInt(value) {
        return new Intl.NumberFormat("ru-RU", {
            maximumFractionDigits: 0,
        }).format(value || 0);
    }

    function fmtMoney(value) {
        return `${new Intl.NumberFormat("ru-RU", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(value || 0)} RUB`;
    }

    function formatDate(value) {
        if (!value) return "—";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return new Intl.DateTimeFormat("ru-RU", {
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
        }).format(date);
    }

    function dayKey(value) {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "—";
        return date.toISOString().slice(0, 10);
    }

    function stringifyValue(value) {
        if (value === null || value === undefined || value === "") return "—";
        if (typeof value === "object") return JSON.stringify(value, null, 2);
        return String(value);
    }

    function firstValue(payload, keys) {
        for (const key of keys) {
            if (payload?.[key] !== undefined && payload[key] !== "")
                return payload[key];
        }
        return "";
    }

    function detectColor(productName) {
        const rule = colorRules.find(([, , pattern]) =>
            pattern.test(productName || ""),
        );
        return rule ? rule[0] : "";
    }

    function colorLabel(colorKey) {
        if (!colorKey) return "Не определён";
        return colorRules.find(([key]) => key === colorKey)?.[1] || colorKey;
    }

    function deliveryInfo(payload, rawText) {
        const deliveryValue = firstValue(payload, [
            "delivery_sum",
            "Delivery",
            "delivery",
            "Доставка",
            "Стоимость доставки",
        ]);
        let deliverySum = parseMoney(deliveryValue);
        for (const pattern of deliveryPatterns) {
            const match = pattern.exec(rawText || "");
            if (match) deliverySum = parseMoney(match[1]);
        }
        return {
            deliveryType: deliverySum > 0 ? "DELIVERY" : "PICKUP",
            deliverySum,
        };
    }

    function normalizeProducts(payload, rawText) {
        const productSource =
            payload?.products ||
            payload?.Products ||
            payload?.Товары ||
            payload?.items ||
            [];
        if (Array.isArray(productSource) && productSource.length) {
            return productSource.map((product) => {
                const productName = stringifyValue(
                    product.name ||
                        product.title ||
                        product.product_name ||
                        product["Название"] ||
                        product["Товар"],
                );
                const itemsCount = Number.parseInt(
                    product.quantity ||
                        product.count ||
                        product.amount ||
                        product.qty ||
                        product["Количество"] ||
                        1,
                    10,
                );
                const itemPrice = parseMoney(
                    product.price ||
                        product.item_price ||
                        product["Цена"] ||
                        product["Стоимость"],
                );
                const size = stringifyValue(
                    product.size || product.Size || product["Размер"] || "",
                ).toUpperCase();
                return {
                    productName,
                    itemsCount: itemsCount || 1,
                    itemPrice,
                    size,
                };
            });
        }

        const matches = [
            ...String(rawText || "").matchAll(
                /^\d+\.\s*(.+?):\s*(\d+)\s*\((\d+)\s*x\s*([\d.,]+)\)\s*Размер:\s*([A-Z0-9]+)/gim,
            ),
        ];
        if (matches.length) {
            return matches.map((match) => ({
                productName: match[1].trim(),
                itemsCount:
                    Number.parseInt(match[3], 10) ||
                    Number.parseInt(match[2], 10) ||
                    1,
                itemPrice: parseMoney(match[4]),
                size: match[5],
            }));
        }

        const productName = firstValue(payload, [
            "product",
            "Product",
            "Товар",
            "Название товара",
        ]);
        if (!productName) return [];
        return [
            {
                productName: stringifyValue(productName),
                itemsCount:
                    Number.parseInt(
                        firstValue(payload, [
                            "quantity",
                            "count",
                            "Количество",
                        ]),
                        10,
                    ) || 1,
                itemPrice: parseMoney(
                    firstValue(payload, ["price", "Цена", "Стоимость"]),
                ),
                size: stringifyValue(
                    firstValue(payload, ["size", "Size", "Размер"]),
                ).toUpperCase(),
            },
        ];
    }

    function normalizeRows(items) {
        return items.flatMap((submission) => {
            const payload = submission.payload || {};
            const rawText = [
                payload.text,
                payload.message,
                payload.Message,
                payload._raw,
            ]
                .filter(Boolean)
                .join("\n");
            const paymentMatch = /Payment Amount:\s*([\d.,]+)\s*RUB/i.exec(
                rawText,
            );
            const orderSumTotal = parseMoney(
                firstValue(payload, [
                    "payment_amount",
                    "Payment Amount",
                    "amount",
                    "total",
                    "sum",
                    "Сумма",
                ]) || paymentMatch?.[1],
            );
            const buyerName = stringifyValue(
                firstValue(payload, [
                    "Name",
                    "name",
                    "Full name",
                    "Имя",
                    "ФИО",
                    "fio",
                ]),
            );
            const phone = stringifyValue(
                firstValue(payload, ["Phone", "phone", "Телефон"]),
            );
            const email = stringifyValue(
                firstValue(payload, ["Email", "email", "Почта"]),
            ).toLowerCase();
            const delivery = deliveryInfo(payload, rawText);
            const products = normalizeProducts(payload, rawText);
            const rows = products.length
                ? products
                : [
                      {
                          productName: "Без товара",
                          itemsCount: 0,
                          itemPrice: 0,
                          size: "",
                      },
                  ];
            return rows.map((product) => {
                const color = detectColor(product.productName);
                return {
                    submissionId: submission.id,
                    createdAt: submission.created_at,
                    date: dayKey(submission.created_at),
                    buyerName,
                    phone,
                    email,
                    productName: product.productName,
                    color,
                    colorLabel: colorLabel(color),
                    size: product.size || "Не указан",
                    itemsCount: product.itemsCount || 0,
                    itemPrice: product.itemPrice || 0,
                    itemsSum:
                        (product.itemsCount || 0) * (product.itemPrice || 0),
                    deliveryType: delivery.deliveryType,
                    deliverySum: delivery.deliverySum,
                    orderSumTotal,
                    source: submission,
                };
            });
        });
    }

    function matchesFilters(row) {
        const query = search?.value.trim().toLowerCase() || "";
        const from = dateFrom?.value || "";
        const to = dateTo?.value || "";
        const delivery = deliveryFilter?.value || "all";
        const size = sizeFilter?.value || "all";
        const color = colorFilter?.value || "all";
        const searchable = JSON.stringify(row).toLowerCase();
        return (
            (!query || searchable.includes(query)) &&
            (!from || row.date >= from) &&
            (!to || row.date <= to) &&
            (delivery === "all" || row.deliveryType === delivery) &&
            (size === "all" || row.size === size) &&
            (color === "all" || row.color === color)
        );
    }

    function aggregate(rows) {
        const orderIds = new Set(rows.map((row) => row.submissionId));
        const buyers = new Set(
            rows
                .map((row) => `${row.phone}|${row.email}`)
                .filter((key) => key !== "|"),
        );
        const uniqueOrders = new Map();
        rows.forEach((row) => uniqueOrders.set(row.submissionId, row));
        const orders = [...uniqueOrders.values()];
        const stats = {
            totalOrders: orderIds.size,
            totalSum: orders.reduce((sum, row) => sum + row.orderSumTotal, 0),
            totalItems: rows.reduce((sum, row) => sum + row.itemsCount, 0),
            deliverySum: orders.reduce((sum, row) => sum + row.deliverySum, 0),
            buyers: buyers.size,
            byDay: new Map(),
            bySize: new Map(),
            byColor: new Map(),
            pickupBySize: new Map(),
        };

        orders.forEach((row) => {
            const item = stats.byDay.get(row.date) || { sum: 0, orders: 0 };
            item.sum += row.orderSumTotal;
            item.orders += 1;
            stats.byDay.set(row.date, item);
        });

        rows.forEach((row) => {
            stats.bySize.set(
                row.size,
                (stats.bySize.get(row.size) || 0) + row.itemsCount,
            );
            stats.byColor.set(
                row.colorLabel,
                (stats.byColor.get(row.colorLabel) || 0) + row.itemsCount,
            );
            if (row.deliveryType === "PICKUP") {
                const item = stats.pickupBySize.get(row.size) || {
                    items: 0,
                    orders: new Set(),
                };
                item.items += row.itemsCount;
                item.orders.add(row.submissionId);
                stats.pickupBySize.set(row.size, item);
            }
        });
        return stats;
    }

    function renderTable(table, headers, rows, maxValueIndex = null) {
        if (!rows.length) {
            table.innerHTML =
                '<tbody><tr><td class="empty-state">Нет данных</td></tr></tbody>';
            return;
        }
        const maxValue =
            maxValueIndex === null
                ? 0
                : Math.max(
                      ...rows.map((row) => Number(row[maxValueIndex]) || 0),
                      1,
                  );
        table.innerHTML = `
            <thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr></thead>
            <tbody>${rows
                .map(
                    (row) => `
                <tr>${row
                    .map((cell, index) => {
                        if (index === maxValueIndex) {
                            const width = Math.round(
                                ((Number(cell) || 0) / maxValue) * 100,
                            );
                            return `<td class="progress-cell">${escapeHtml(cell)}<div class="progress-bar"><span style="width:${width}%"></span></div></td>`;
                        }
                        return `<td>${escapeHtml(cell)}</td>`;
                    })
                    .join("")}</tr>
            `,
                )
                .join("")}</tbody>`;
    }

    function renderStats(rows) {
        const stats = aggregate(rows);
        summaryEls.orders.textContent = fmtInt(stats.totalOrders);
        summaryEls.sum.textContent = fmtMoney(stats.totalSum);
        summaryEls.items.textContent = fmtInt(stats.totalItems);
        summaryEls.delivery.textContent = fmtMoney(stats.deliverySum);
        summaryEls.buyers.textContent = fmtInt(stats.buyers);
        summaryEls.last.textContent = submissions[0]
            ? formatDate(submissions[0].created_at)
            : "—";

        renderTable(
            daysTable,
            ["Дата", "Сумма", "Заказов"],
            [...stats.byDay.entries()]
                .sort(([a], [b]) => b.localeCompare(a))
                .map(([date, item]) => [
                    date,
                    fmtMoney(item.sum),
                    fmtInt(item.orders),
                ]),
        );
        renderTable(
            sizesTable,
            ["Размер", "Количество"],
            [...stats.bySize.entries()]
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([size, count]) => [size, count]),
            1,
        );
        renderTable(
            colorsTable,
            ["Цвет", "Количество"],
            [...stats.byColor.entries()]
                .sort((a, b) => b[1] - a[1])
                .map(([color, count]) => [color, count]),
            1,
        );
        renderTable(
            pickupTable,
            ["Размер", "Штук", "Заказов"],
            [...stats.pickupBySize.entries()]
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([size, item]) => [size, item.items, item.orders.size]),
            1,
        );
    }

    function renderFields(payload) {
        const entries = Object.entries(payload || {});
        if (!entries.length)
            return '<div class="empty-state">В заявке нет полей payload.</div>';
        return `<div class="field-grid">${entries
            .map(
                ([key, value]) => `
            <div class="field"><span class="field-key">${escapeHtml(key)}</span><div class="field-value">${escapeHtml(stringifyValue(value))}</div></div>
        `,
            )
            .join("")}</div>`;
    }

    function renderSubmission(submission) {
        const rows = orderRows.filter(
            (row) => row.submissionId === submission.id,
        );
        const raw = JSON.stringify(submission, null, 2);
        const orderSum = rows[0]?.orderSumTotal || 0;
        const items = rows.reduce((sum, row) => sum + row.itemsCount, 0);
        return `
            <article class="submission-card">
                <header class="submission-header">
                    <div>
                        <h2 class="submission-title">${escapeHtml(submission.customer_name || "Без имени")}</h2>
                        <div class="submission-meta">
                            <span class="badge">${escapeHtml(formatDate(submission.created_at))}</span>
                            <span class="badge">${escapeHtml(submission.contact || "Без контакта")}</span>
                            <span class="badge">${escapeHtml(fmtMoney(orderSum))}</span>
                            <span class="badge warning">${escapeHtml(fmtInt(items))} шт</span>
                        </div>
                    </div>
                    <span class="badge">${escapeHtml(submission.id)}</span>
                </header>
                <div class="submission-body">
                    ${renderFields(submission.payload)}
                    <details class="details"><summary>Технические данные</summary><pre>${escapeHtml(raw)}</pre></details>
                </div>
            </article>`;
    }

    function updateFilterOptions() {
        const selectedSize = sizeFilter.value;
        const selectedColor = colorFilter.value;
        const sizes = [
            ...new Set(orderRows.map((row) => row.size).filter(Boolean)),
        ].sort();
        const colors = [
            ...new Map(
                orderRows.map((row) => [row.color, row.colorLabel]),
            ).entries(),
        ]
            .filter(([key]) => key)
            .sort((a, b) => a[1].localeCompare(b[1]));
        sizeFilter.innerHTML =
            '<option value="all">Все размеры</option>' +
            sizes
                .map(
                    (size) =>
                        `<option value="${escapeHtml(size)}">${escapeHtml(size)}</option>`,
                )
                .join("");
        colorFilter.innerHTML =
            '<option value="all">Все цвета</option>' +
            colors
                .map(
                    ([key, label]) =>
                        `<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`,
                )
                .join("");
        if (sizes.includes(selectedSize)) sizeFilter.value = selectedSize;
        if (colors.some(([key]) => key === selectedColor))
            colorFilter.value = selectedColor;
    }

    function render() {
        visibleRows = orderRows.filter(matchesFilters);
        const visibleSubmissionIds = new Set(
            visibleRows.map((row) => row.submissionId),
        );
        const visibleSubmissions = submissions.filter((submission) =>
            visibleSubmissionIds.has(submission.id),
        );
        renderStats(visibleRows);
        visibleCount.textContent = `${fmtInt(visibleSubmissions.length)} заявок / ${fmtInt(visibleRows.length)} строк товаров`;
        status.textContent = `Показано: ${fmtInt(visibleSubmissions.length)} заявок, ${fmtInt(visibleRows.length)} товарных строк`;
        list.innerHTML = visibleSubmissions.length
            ? visibleSubmissions.map(renderSubmission).join("")
            : '<div class="empty-state">Заявок пока нет или ничего не найдено.</div>';
    }

    function download(filename, content, type) {
        const blob = new Blob([content], { type });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        link.click();
        URL.revokeObjectURL(url);
    }

    function exportVisibleCsv() {
        const headers = [
            "date",
            "buyerName",
            "phone",
            "email",
            "productName",
            "colorLabel",
            "size",
            "itemsCount",
            "itemPrice",
            "itemsSum",
            "deliveryType",
            "deliverySum",
            "orderSumTotal",
        ];
        const lines = [headers.join(";")].concat(
            visibleRows.map((row) =>
                headers
                    .map(
                        (key) =>
                            `"${String(row[key] ?? "").replaceAll('"', '""')}"`,
                    )
                    .join(";"),
            ),
        );
        download(
            `${siteName}_orders.csv`,
            `\ufeff${lines.join("\n")}`,
            "text/csv;charset=utf-8",
        );
    }

    function exportVisibleJson() {
        download(
            `${siteName}_orders.json`,
            JSON.stringify(visibleRows, null, 2),
            "application/json;charset=utf-8",
        );
    }

    async function loadSubmissions() {
        status.textContent = "Загружаем заявки...";
        refresh.disabled = true;
        try {
            const params = new URLSearchParams();
            if (secret) params.set("secret", secret);
            const response = await fetch(
                `/tilda/${encodeURIComponent(siteName)}/form/submissions?${params}`,
                {
                    headers: { Accept: "application/json" },
                },
            );
            if (!response.ok)
                throw new Error(
                    response.status === 403
                        ? "Неверный secret"
                        : `HTTP ${response.status}`,
                );
            const data = await response.json();
            submissions = Array.isArray(data.submissions)
                ? data.submissions
                : [];
            orderRows = normalizeRows(submissions);
            updateFilterOptions();
            render();
        } catch (error) {
            status.textContent = "Не удалось загрузить заявки";
            list.innerHTML = `<div class="empty-state">Ошибка загрузки: ${escapeHtml(error.message)}</div>`;
        } finally {
            refresh.disabled = false;
        }
    }

    [search, dateFrom, dateTo, deliveryFilter, sizeFilter, colorFilter].forEach(
        (element) => {
            element?.addEventListener("input", render);
            element?.addEventListener("change", render);
        },
    );
    refresh?.addEventListener("click", loadSubmissions);
    exportCsv?.addEventListener("click", exportVisibleCsv);
    exportJson?.addEventListener("click", exportVisibleJson);
    loadSubmissions();
})();
