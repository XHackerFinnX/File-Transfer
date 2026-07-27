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
    const productTypeFilter = document.querySelector(
        "[data-product-type-filter]",
    );
    const colorFilter = document.querySelector("[data-color-filter]");
    const socialFilter = document.querySelector("[data-social-filter]");
    const visibleCount = document.querySelector("[data-visible-count]");
    const paginationContainers = document.querySelectorAll("[data-pagination]");
    const daysTable = document.querySelector("[data-days-table]");
    const sizesTable = document.querySelector("[data-sizes-table]");
    const colorsTable = document.querySelector("[data-colors-table]");
    const pickupTable = document.querySelector("[data-pickup-table]");
    const internationalTable = document.querySelector(
        "[data-international-table]",
    );
    const cdekTable = document.querySelector("[data-cdek-table]");
    const deliveryOrderCountEls = {
        cdek: document.querySelector("[data-cdek-orders]"),
        pickup: document.querySelector("[data-pickup-orders]"),
        international: document.querySelector("[data-international-orders]"),
    };
    const exportCsv = document.querySelector("[data-export-csv]");
    const exportJson = document.querySelector("[data-export-json]");
    const resetFiltersButton = document.querySelector("[data-reset-filters]");
    const summaryEls = {
        orders: document.querySelector("[data-total-orders]"),
        sum: document.querySelector("[data-total-sum]"),
        items: document.querySelector("[data-total-items]"),
        delivery: document.querySelector("[data-delivery-sum]"),
        products: document.querySelector("[data-products-sum]"),
        buyers: document.querySelector("[data-unique-buyers]"),
        last: document.querySelector("[data-last]"),
    };

    let submissions = [];
    let orderRows = [];
    let visibleRows = [];
    let currentPage = 1;
    let pageSize = 10;

    const colorRules = [
        ["white", "Белые", /(^|[^а-яё])бел[а-яё]*/i],
        ["black", "Чёрные", /(^|[^а-яё])ч[её]рн[а-яё]*/i],
        ["blue", "Синие", /(^|[^а-яё])син[а-яё]*/i],
        ["red", "Красные", /(^|[^а-яё])крас[а-яё]*/i],
        ["green", "Зелёные", /(^|[^а-яё])зел[а-яё]*/i],
        ["gray", "Серые", /(^|[^а-яё])сер[а-яё]*/i],
        ["pink", "Розовый", /(^|[^а-яё])роз[а-яё]*/i],
        ["cyan", "Голубой", /(^|[^а-яё])голу[а-яё]*/i],
    ];

    const socialNetworkMeta = {
        instagram: { label: "Instagram", icon: "instagram" },
        tiktok: { label: "TikTok", icon: "tiktok" },
        telegram: { label: "Telegram", icon: "telegram" },
        vk: { label: "ВКонтакте", icon: "vk" },
        max: { label: "MAX", icon: "max" },
        youtube: { label: "YouTube", icon: "youtube" },
        none: { label: "Без соц. сети", icon: "minus" },
    };

    function socialNetworkInfo(value) {
        const key = String(value || "")
            .trim()
            .toLowerCase();
        return socialNetworkMeta[key]
            ? { key, ...socialNetworkMeta[key] }
            : { key: "none", ...socialNetworkMeta.none };
    }

    function parseCookieString(value) {
        return String(value || "")
            .split(";")
            .map((part) => part.trim())
            .filter(Boolean)
            .reduce((acc, part) => {
                const separator = part.indexOf("=");
                if (separator === -1) return acc;
                const key = part.slice(0, separator).trim();
                const rawValue = part.slice(separator + 1).trim();
                try {
                    acc[key] = decodeURIComponent(rawValue);
                } catch {
                    acc[key] = rawValue;
                }
                return acc;
            }, {});
    }

    function submissionSocialNetwork(submission) {
        const payloadCookies = parseCookieString(submission.payload?.COOKIES);
        const requestCookies = submission.cookies || {};
        const value =
            payloadCookies.social_network ||
            requestCookies.social_network ||
            "";
        return socialNetworkInfo(value === "myself" ? "" : value);
    }

    const deliveryPatterns = [
        /Доставка в ПВЗ СДЭК:\s*([\d.,]+)/i,
        /Доставка СДЭК по России:\s*([\d.,]+)/i,
        /Международная доставка:\s*([\d.,]+)/i,
        /International Delivery:\s*([\d.,]+)/i,
        /Доставка[^\d]*([\d.,]+)/i,
    ];

    // Утилита: отложенный вызов (debounce) — чтобы поиск не
    // перерисовывал весь список на каждое нажатие клавиши.
    function debounce(fn, delay = 200) {
        let timer;
        return (...args) => {
            clearTimeout(timer);
            timer = setTimeout(() => fn(...args), delay);
        };
    }

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

    // Исправление: раньше использовался toISOString(), из-за чего
    // заявки, созданные вечером, попадали в «соседний» день (UTC-сдвиг).
    // Теперь ключ дня строится по локальному времени.
    function dayKey(value) {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "—";
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, "0");
        const day = String(date.getDate()).padStart(2, "0");
        return `${year}-${month}-${day}`;
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

    function asObject(value) {
        return value && typeof value === "object" && !Array.isArray(value)
            ? value
            : {};
    }

    function paymentPayload(payload) {
        return asObject(
            payload?.payment || payload?.Payment || payload?.Оплата,
        );
    }

    function optionValue(product, optionNames) {
        const direct = firstValue(product, optionNames);
        if (direct) return direct;
        const options = Array.isArray(product?.options) ? product.options : [];
        const normalizedNames = optionNames.map((name) =>
            String(name).toLowerCase(),
        );
        const found = options.find((item) =>
            normalizedNames.includes(
                String(item?.option || item?.name || "").toLowerCase(),
            ),
        );
        return found?.variant || found?.value || "";
    }

    function normalizeDeliveryType(deliveryText, deliverySum) {
        const text = String(deliveryText || "").toLowerCase();
        if (/самовывоз|pickup self|self[-\s]?pickup/.test(text))
            return "PICKUP";
        if (/международ|international/.test(text)) return "INTERNATIONAL";
        if (/доставка|пвз|сдэк|delivery|point/.test(text)) return "DELIVERY";
        return deliverySum > 0 ? "DELIVERY" : "PICKUP";
    }

    function detectColor(productName, explicitColor = "") {
        const value = explicitColor || productName || "";
        const rule = colorRules.find(([, , pattern]) => pattern.test(value));
        return rule ? rule[0] : String(explicitColor || "").trim();
    }

    function detectProductType(productName, explicitType = "", sku = "") {
        const candidates = [explicitType, productName, sku]
            .map((value) => String(value || "").trim())
            .filter((value) => value && value !== "—");
        const source = candidates[0] || "";
        const searchable = candidates.join(" ").toLowerCase();
        if (!searchable) return "Не определён";
        const typeRules = [
            ["Худи", /(зип[-\s]*)?худи|hoodie/i],
            ["Штаны", /штаны|брюки|pants|trousers/i],
            ["Футболка", /футболк|t-?shirt/i],
            ["Лонгслив", /лонгслив|longsleeve|long sleeve/i],
            ["Свитшот", /свитшот|sweatshirt/i],
            ["Куртка", /куртк|jacket/i],
            ["Шорты", /шорт|shorts/i],
            ["Кепка", /кепк|cap/i],
        ];
        const found = typeRules.find(([, pattern]) => pattern.test(searchable));
        if (found) return found[0];
        return (
            source
                .replace(
                    /(бел\w*|ч[её]рн\w*|син\w*|крас\w*|зел\w*|сер\w*|роз\w*|голу\w*)/gi,
                    "",
                )
                .replace(/\b(xs|s|m|l|xl|xxl|xxxl|\d{2,3})\b/gi, "")
                .replace(/[—–-]+/g, " ")
                .replace(/\s+/g, " ")
                .trim() || source
        );
    }

    function colorLabel(colorKey) {
        if (!colorKey) return "Не определён";
        return colorRules.find(([key]) => key === colorKey)?.[1] || colorKey;
    }

    function deliveryInfo(payload, rawText) {
        const payment = paymentPayload(payload);
        const deliveryValue =
            payment.delivery_price ??
            firstValue(payload, [
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
        const deliveryText =
            payment.delivery || firstValue(payload, ["delivery", "Доставка"]);
        return {
            deliveryType: normalizeDeliveryType(deliveryText, deliverySum),
            deliverySum,
            deliveryText: stringifyValue(deliveryText),
            deliveryAddress: stringifyValue(payment.delivery_address || ""),
            pickupId: stringifyValue(payment.delivery_pickup_id || ""),
            deliveryFio: stringifyValue(payment.delivery_fio || ""),
            deliveryCity: stringifyValue(payment.delivery_city || ""),
            deliveryZip: stringifyValue(payment.delivery_zip || ""),
            deliveryComment: stringifyValue(payment.delivery_comment || ""),
        };
    }

    function normalizeProducts(payload, rawText) {
        const payment = paymentPayload(payload);
        const productSource =
            payment.products ||
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
                    optionValue(product, ["Размер", "size", "Size"]) || "",
                ).toUpperCase();
                const colorName = stringifyValue(
                    optionValue(product, ["Цвет", "color", "Color"]) || "",
                );
                return {
                    productName,
                    sku: stringifyValue(product.sku || ""),
                    externalId: stringifyValue(
                        product.externalid || product.externalId || "",
                    ),
                    itemsCount: itemsCount || 1,
                    itemPrice,
                    size,
                    colorName,
                    productType:
                        product.type ||
                        product.category ||
                        product.product_type ||
                        optionValue(product, [
                            "Тип товара",
                            "type",
                            "category",
                        ]),
                    options: Array.isArray(product.options)
                        ? product.options
                        : [],
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
            const payment = paymentPayload(payload);
            const paymentMatch = /Payment Amount:\s*([\d.,]+)\s*RUB/i.exec(
                rawText,
            );
            const orderSumTotal = parseMoney(
                payment.amount ||
                    firstValue(payload, [
                        "payment_amount",
                        "Payment Amount",
                        "amount",
                        "total",
                        "sum",
                        "Сумма",
                    ]) ||
                    paymentMatch?.[1],
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
            const orderId = stringifyValue(
                payment.orderid || payment.order_id || "",
            );
            const paymentSystem = stringifyValue(
                payment.sys ||
                    firstValue(payload, ["paymentsystem", "paymentSystem"]),
            );
            const socialNetwork = submissionSocialNetwork(submission);
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
            const submissionImNumber = stringifyValue(
                submission.im_number || "",
            );
            return rows.map((product) => {
                const color = detectColor(
                    product.productName,
                    product.colorName,
                );
                const row = {
                    submissionId: submission.id,
                    createdAt: submission.created_at,
                    date: dayKey(submission.created_at),
                    buyerName,
                    phone,
                    email,
                    orderId,
                    imNumber:
                        submissionImNumber && submissionImNumber !== "—"
                            ? submissionImNumber
                            : orderId,
                    paymentSystem,
                    productName: product.productName,
                    sku: product.sku || "",
                    externalId: product.externalId || "",
                    color,
                    colorLabel: colorLabel(color),
                    colorName: product.colorName || "",
                    productType: detectProductType(
                        product.productName,
                        product.productType,
                        product.sku,
                    ),
                    size: product.size || "Не указан",
                    itemsCount: product.itemsCount || 0,
                    itemPrice: product.itemPrice || 0,
                    itemsSum:
                        (product.itemsCount || 0) * (product.itemPrice || 0),
                    deliveryType: delivery.deliveryType,
                    deliverySum: delivery.deliverySum,
                    deliveryText: delivery.deliveryText,
                    deliveryAddress: delivery.deliveryAddress,
                    pickupId: delivery.pickupId,
                    deliveryFio: delivery.deliveryFio,
                    deliveryCity: delivery.deliveryCity,
                    deliveryZip: delivery.deliveryZip,
                    deliveryComment: delivery.deliveryComment,
                    orderSumTotal,
                    socialNetwork: socialNetwork.key,
                    socialNetworkLabel: socialNetwork.label,
                    socialNetworkIcon: socialNetwork.icon,
                    source: submission,
                };
                // Оптимизация: поисковая строка считается один раз при
                // нормализации, а не JSON.stringify на каждый ввод символа.
                row._search = [
                    buyerName,
                    phone,
                    email,
                    orderId,
                    row.imNumber,
                    row.productName,
                    row.sku,
                    row.colorName,
                    row.colorLabel,
                    row.productType,
                    row.size,
                    row.deliveryText,
                    row.deliveryAddress,
                    row.deliveryCity,
                    row.deliveryFio,
                    row.socialNetworkLabel,
                ]
                    .join(" ")
                    .toLowerCase();
                return row;
            });
        });
    }

    function matchesFilters(row) {
        const query = search?.value.trim().toLowerCase() || "";
        const from = dateFrom?.value || "";
        const to = dateTo?.value || "";
        const delivery = deliveryFilter?.value || "all";
        const size = sizeFilter?.value || "all";
        const productType = productTypeFilter?.value || "all";
        const color = colorFilter?.value || "all";
        const social = socialFilter?.value || "all";
        return (
            (!query || row._search.includes(query)) &&
            (!from || row.date >= from) &&
            (!to || row.date <= to) &&
            (delivery === "all" || row.deliveryType === delivery) &&
            (productType === "all" || row.productType === productType) &&
            (size === "all" || row.size === size) &&
            (color === "all" || row.color === color) &&
            (social === "all" || row.socialNetwork === social)
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
        rows.forEach((row) => {
            const order = uniqueOrders.get(row.submissionId) || {
                ...row,
                productsSum: 0,
            };
            order.productsSum += row.itemsSum;
            uniqueOrders.set(row.submissionId, order);
        });
        const orders = [...uniqueOrders.values()].map((order) => {
            const fallbackTotal = order.productsSum + order.deliverySum;
            const total = order.orderSumTotal || fallbackTotal;
            return {
                ...order,
                orderSumTotal: total,
                productsSum: Math.max(total - order.deliverySum, 0),
            };
        });
        const stats = {
            totalOrders: orderIds.size,
            totalSum: orders.reduce((sum, row) => sum + row.orderSumTotal, 0),
            totalItems: rows.reduce((sum, row) => sum + row.itemsCount, 0),
            productsSum: orders.reduce((sum, row) => sum + row.productsSum, 0),
            deliverySum: orders.reduce((sum, row) => sum + row.deliverySum, 0),
            buyers: buyers.size,
            byDay: new Map(),
            bySize: new Map(),
            byColor: new Map(),
            pickupBySize: new Map(),
            pickupOrders: new Set(),
            cdekOrders: new Set(),
            internationalOrders: new Set(),
            cdekBySize: new Map(),
            internationalBySize: new Map(),
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
                stats.pickupOrders.add(row.submissionId);
                stats.pickupBySize.set(row.size, item);
            }
            if (row.deliveryType === "INTERNATIONAL") {
                const item = stats.internationalBySize.get(row.size) || {
                    items: 0,
                    orders: new Set(),
                };
                item.items += row.itemsCount;
                item.orders.add(row.submissionId);
                stats.internationalOrders.add(row.submissionId);
                stats.internationalBySize.set(row.size, item);
            }
            if (row.deliveryType === "DELIVERY") {
                const item = stats.cdekBySize.get(row.size) || {
                    items: 0,
                    orders: new Set(),
                };
                item.items += row.itemsCount;
                item.orders.add(row.submissionId);
                stats.cdekOrders.add(row.submissionId);
                stats.cdekBySize.set(row.size, item);
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
            <thead><tr>${headers.map((header) => `<th scope="col">${escapeHtml(header)}</th>`).join("")}</tr></thead>
            <tbody>${rows
                .map(
                    (row) => `
                <tr>${row
                    .map((cell, index) => {
                        if (index === maxValueIndex) {
                            const width = Math.round(
                                ((Number(cell) || 0) / maxValue) * 100,
                            );
                            return `<td class="progress-cell">${escapeHtml(cell)}<div class="progress-bar" role="presentation"><span style="width:${width}%"></span></div></td>`;
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
        summaryEls.products.textContent = fmtMoney(stats.productsSum);
        summaryEls.buyers.textContent = fmtInt(stats.buyers);
        summaryEls.last.textContent = submissions[0]
            ? formatDate(submissions[0].created_at)
            : "—";
        deliveryOrderCountEls.cdek.textContent = `${fmtInt(stats.cdekOrders.size)} заказов`;
        deliveryOrderCountEls.pickup.textContent = `${fmtInt(stats.pickupOrders.size)} заказов`;
        deliveryOrderCountEls.international.textContent = `${fmtInt(stats.internationalOrders.size)} заказов`;

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
        renderTable(
            cdekTable,
            ["Размер", "Штук", "Заказов"],
            [...stats.cdekBySize.entries()]
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([size, item]) => [size, item.items, item.orders.size]),
            1,
        );
        renderTable(
            internationalTable,
            ["Размер", "Штук", "Заказов"],
            [...stats.internationalBySize.entries()]
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([size, item]) => [size, item.items, item.orders.size]),
            1,
        );
    }

    function renderKeyValue(label, value) {
        if (
            value === null ||
            value === undefined ||
            value === "" ||
            value === "—"
        )
            return "";
        return `<div class="info-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
    }

    function renderProductsTable(rows) {
        if (!rows.length)
            return '<div class="empty-state">Товаров в заявке не найдено.</div>';
        return `<div class="table-wrap compact"><table>
            <thead><tr><th scope="col">Товар</th><th scope="col">SKU</th><th scope="col">Цвет</th><th scope="col">Размер</th><th scope="col">Кол-во</th><th scope="col">Цена</th><th scope="col">Сумма</th></tr></thead>
            <tbody>${rows
                .map(
                    (row) => `<tr>
                        <td>${escapeHtml(row.productName)}</td>
                        <td>${escapeHtml(row.sku || "—")}</td>
                        <td>${escapeHtml(row.colorName || row.colorLabel)}</td>
                        <td>${escapeHtml(row.size)}</td>
                        <td>${escapeHtml(fmtInt(row.itemsCount))}</td>
                        <td>${escapeHtml(fmtMoney(row.itemPrice))}</td>
                        <td>${escapeHtml(fmtMoney(row.itemsSum))}</td>
                    </tr>`,
                )
                .join("")}</tbody>
        </table></div>`;
    }

    function renderCustomerSection(submission, rows) {
        const payload = submission.payload || {};
        const first = rows[0] || {};
        return `<section class="pretty-section">
            <h3>Покупатель</h3>
            <div class="info-grid">
                ${renderKeyValue("Имя", first.buyerName || submission.customer_name)}
                ${renderKeyValue("Телефон", first.phone || submission.contact)}
                ${renderKeyValue("Email", first.email)}
                ${renderKeyValue("ФИО доставки", first.deliveryFio)}
                ${renderKeyValue("Согласие", firstValue(payload, ["Checkbox", "checkbox"]))}
                ${renderKeyValue("Соц. сеть", first.socialNetworkLabel)}
            </div>
        </section>`;
    }

    function renderPaymentSection(rows) {
        const first = rows[0] || {};
        const subtotal = rows.reduce((sum, row) => sum + row.itemsSum, 0);
        return `<section class="pretty-section">
            <h3>Оплата и доставка</h3>
            <div class="info-grid">
                ${renderKeyValue("Номер заказа", first.orderId)}
                ${renderKeyValue("Номер ИМ", first.imNumber)}
                ${renderKeyValue("Платёжная система", first.paymentSystem)}
                ${renderKeyValue("Товары", fmtMoney(subtotal))}
                ${renderKeyValue("Доставка", fmtMoney(first.deliverySum || 0))}
                ${renderKeyValue("Итого", fmtMoney(first.orderSumTotal || subtotal + (first.deliverySum || 0)))}
                ${renderKeyValue("Тип доставки", first.deliveryText || (first.deliveryType === "PICKUP" ? "Самовывоз" : first.deliveryType === "INTERNATIONAL" ? "Международные заказы" : "Доставка"))}
                ${renderKeyValue("ПВЗ", first.pickupId)}
                ${renderKeyValue("Город", first.deliveryCity)}
                ${renderKeyValue("Индекс", first.deliveryZip)}
                ${renderKeyValue("Адрес", first.deliveryAddress)}
                ${renderKeyValue("Комментарий", first.deliveryComment)}
            </div>
        </section>`;
    }

    function renderExtraFields(payload) {
        const skipped = new Set([
            "payment",
            "Payment",
            "Оплата",
            "Name",
            "name",
            "Phone",
            "phone",
            "Email",
            "email",
        ]);
        const entries = Object.entries(payload || {}).filter(
            ([key]) => !skipped.has(key),
        );
        if (!entries.length) return "";
        return `<section class="pretty-section"><h3>Дополнительные поля формы</h3><div class="field-grid">${entries
            .map(
                ([key, value]) =>
                    `<div class="field"><span class="field-key">${escapeHtml(key)}</span><div class="field-value">${escapeHtml(stringifyValue(value))}</div></div>`,
            )
            .join("")}</div></section>`;
    }

    function customerEmail(first, submission) {
        const email =
            first.email ||
            submission.payload?.Email ||
            submission.payload?.email ||
            "";
        return email && email !== "—" ? email : "";
    }

    function emailStats(submission) {
        const messages = Array.isArray(submission.email_messages)
            ? submission.email_messages
            : [];
        const orderCount = messages.filter(
            (message) => message.message_type === "order_notification",
        ).length;
        const customCount = messages.filter(
            (message) => message.message_type === "custom_message",
        ).length;
        return { messages, orderCount, customCount, total: messages.length };
    }

    function renderEmailBadges(submission) {
        const stats = emailStats(submission);
        if (!stats.total) return "";
        return `<span class="badge email-sent">Писем: ${escapeHtml(fmtInt(stats.total))}</span>`;
    }

    function renderEmailHistory(submission) {
        const { messages } = emailStats(submission);
        if (!messages.length) {
            return `<details class="details email-history wide"><summary>История писем</summary><div class="empty-state compact">Письма этому клиенту ещё не отправлялись.</div></details>`;
        }
        return `<details class="details email-history wide"><summary>История писем · ${escapeHtml(fmtInt(messages.length))}</summary>
            <div class="email-history-list">
                ${messages
                    .map(
                        (
                            message,
                        ) => `<article class="email-history-item ${message.message_type === "order_notification" ? "is-order" : "is-custom"}">
                            <div class="email-history-head">
                                <span class="email-history-type">${message.message_type === "order_notification" ? "Уведомление о заказе" : "Письмо администратора"}</span>
                                <span>${escapeHtml(formatDate(message.created_at))}</span>
                            </div>
                            <h4>${escapeHtml(message.subject || "Без темы")}</h4>
                            <p>${escapeHtml(message.body || "")}</p>
                        </article>`,
                    )
                    .join("")}
            </div>
        </details>`;
    }

    function openEmailModal(button) {
        const submission = submissions.find(
            (item) => item.id === button.dataset.submissionId,
        );
        if (!submission) return;
        const rows = orderRows.filter(
            (row) => row.submissionId === submission.id,
        );
        const first = rows[0] || {};
        const subtotal = rows.reduce((sum, row) => sum + row.itemsSum, 0);
        const email = customerEmail(first, submission);
        const modal = document.createElement("div");
        modal.className = "email-modal-backdrop";
        modal.innerHTML = `
            <div class="email-modal" role="dialog" aria-modal="true" aria-label="Отправка письма клиенту">
                <button class="email-modal-close" type="button" data-email-close>×</button>
                <div class="email-modal-hero">
                    <span>Письмо клиенту</span>
                    <h2>${escapeHtml(first.buyerName || submission.customer_name || "Покупатель")}</h2>
                    <p>${escapeHtml(email || "Email не найден")}</p>
                </div>
                <div class="email-modal-content">
                    <label class="email-choice"><input type="radio" name="email-mode" value="order_notification" checked> <span><b>Отправить уведомление о заказе</b><small>Письмо с названием магазина, номером заказа, суммой и доставкой.</small></span></label>
                    <label class="email-choice"><input type="radio" name="email-mode" value="custom_message"> <span><b>Написать письмо</b><small>Вводим тему и текст сообщения.</small></span></label>
                    <div class="custom-email-fields" hidden>
                        <label>Тема<input data-email-subject type="text" placeholder="Сообщение по вашему заказу"></label>
                        <label>Текст<textarea data-email-body rows="7" placeholder="Введите текст письма"></textarea></label>
                    </div>
                    <div class="email-preview">
                        <b>Данные для уведомления</b>
                        <span>Заказ: ${escapeHtml(first.orderId || submission.id)}</span>
                        <span>Итого: ${escapeHtml(fmtMoney(first.orderSumTotal || subtotal + (first.deliverySum || 0)))}</span>
                        <span>Доставка: ${escapeHtml(first.deliveryText || first.deliveryType || "Не указана")}, ${escapeHtml(fmtMoney(first.deliverySum || 0))}</span>
                    </div>
                    <div class="email-modal-actions">
                        <button class="ghost-button" type="button" data-email-close>Отмена</button>
                        <button class="action-button email-send-button" type="button" data-email-send>Отправить</button>
                    </div>
                </div>
            </div>`;
        document.body.append(modal);

        modal.addEventListener("change", (event) => {
            if (event.target.name === "email-mode") {
                modal.querySelector(".custom-email-fields").hidden =
                    event.target.value !== "custom_message";
            }
        });
        modal.addEventListener("click", async (event) => {
            if (
                event.target.closest("[data-email-close]") ||
                event.target === modal
            ) {
                modal.remove();
                return;
            }
            const sendButton = event.target.closest("[data-email-send]");
            if (!sendButton) return;
            const mode = modal.querySelector(
                'input[name="email-mode"]:checked',
            )?.value;
            const subject =
                modal.querySelector("[data-email-subject]")?.value || "";
            const body = modal.querySelector("[data-email-body]")?.value || "";
            if (!email) {
                window.alert("У этой заявки нет email клиента.");
                return;
            }
            if (mode === "custom_message" && !body.trim()) {
                window.alert("Введите текст письма.");
                return;
            }
            sendButton.setAttribute("aria-busy", "true");
            sendButton.textContent = "Отправляем...";
            try {
                const params = new URLSearchParams();
                if (secret) params.set("secret", secret);
                const response = await fetch(
                    `/tilda/${encodeURIComponent(siteName)}/form/email/send?${params}`,
                    {
                        method: "POST",
                        headers: {
                            Accept: "application/json",
                            "Content-Type": "application/json",
                        },
                        body: JSON.stringify({
                            submission_id: submission.id,
                            message_type: mode,
                            to_email: email,
                            customer_name:
                                first.buyerName ||
                                submission.customer_name ||
                                "",
                            order_id: first.orderId || submission.id,
                            order_sum: fmtMoney(
                                first.orderSumTotal ||
                                    subtotal + (first.deliverySum || 0),
                            ),
                            delivery_sum: fmtMoney(first.deliverySum || 0),
                            delivery_text:
                                first.deliveryText ||
                                first.deliveryType ||
                                "Доставка не указана",
                            custom_subject: subject,
                            custom_body: body,
                        }),
                    },
                );
                const data = await response.json().catch(() => ({}));
                if (!response.ok || !data.ok)
                    throw new Error(
                        data.detail || data.error || `HTTP ${response.status}`,
                    );
                modal.remove();
                await loadSubmissions();
            } catch (error) {
                window.alert(`Не удалось отправить письмо: ${error.message}`);
                sendButton.removeAttribute("aria-busy");
                sendButton.textContent = "Отправить";
            }
        });
    }

    async function changeCdekImNumber(button) {
        const currentImNumber = button.dataset.currentImNumber || "";
        const defaultNewNumber =
            button.dataset.defaultImNumber || currentImNumber;
        const newImNumber = window.prompt(
            "Новый Номер ИМ в СДЭК",
            defaultNewNumber,
        );
        if (!newImNumber || newImNumber.trim() === currentImNumber) return;

        button.setAttribute("aria-busy", "true");
        button.textContent = "Меняем...";
        try {
            const params = new URLSearchParams();
            if (secret) params.set("secret", secret);
            const response = await fetch(
                `/tilda/${encodeURIComponent(siteName)}/form/cdek/im-number?${params}`,
                {
                    method: "POST",
                    headers: {
                        Accept: "application/json",
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({
                        current_im_number: currentImNumber,
                        new_im_number: newImNumber.trim(),
                        order_id: button.dataset.orderId || "",
                        submission_id: button.dataset.submissionId || "",
                    }),
                },
            );
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.ok) {
                throw new Error(
                    data.detail || data.error || `HTTP ${response.status}`,
                );
            }
            window.alert(`Номер ИМ изменён на ${data.new_im_number}`);
            await loadSubmissions();
        } catch (error) {
            window.alert(`Не удалось изменить Номер ИМ: ${error.message}`);
        } finally {
            button.removeAttribute("aria-busy");
            button.textContent = "Изменить Номер ИМ";
        }
    }

    function renderSubmission(submission) {
        const rows = orderRows.filter(
            (row) => row.submissionId === submission.id,
        );
        const raw = JSON.stringify(submission, null, 2);
        const first = rows[0] || {};
        const orderSum =
            first.orderSumTotal ||
            rows.reduce((sum, row) => sum + row.itemsSum, 0);
        const items = rows.reduce((sum, row) => sum + row.itemsCount, 0);
        const title =
            first.buyerName || submission.customer_name || "Без имени";

        const email = customerEmail(first, submission);
        const orderId = first.orderId || "";
        const imNumber = first.imNumber || orderId;
        const isCustomImNumber = Boolean(
            orderId && imNumber && imNumber !== orderId,
        );
        return `
            <article class="submission-card">
                <details class="submission-toggle">
                    <summary class="submission-header">
                        <div>
                            <h2 class="submission-title">${escapeHtml(title)}</h2>
                            <div class="submission-meta">
                                <span class="badge">${escapeHtml(formatDate(submission.created_at))}</span>
                                <span class="badge">${escapeHtml(fmtMoney(orderSum))}</span>
                                <span class="badge warning">${escapeHtml(fmtInt(items))} шт</span>
                                ${first.socialNetworkLabel ? `<span class="badge social-badge" data-social-icon="${escapeHtml(first.socialNetworkIcon)}">${escapeHtml(first.socialNetworkLabel)}</span>` : ""}
                                ${renderEmailBadges(submission)}
                                ${imNumber ? `<span class="badge ${isCustomImNumber ? "im-custom" : ""}">${escapeHtml(`Номер ИМ ${imNumber}`)}</span>` : ""}
                            </div>
                        </div>
                        <span class="submission-actions">
                            <span class="toggle-hint">Подробнее</span>
                        </span>
                    </summary>
                    <div class="submission-body pretty-body">
                        ${
                            email || imNumber
                                ? `<div class="submission-actions submission-body-actions">
                                ${email ? `<button class="action-button" type="button" data-email-modal data-submission-id="${escapeHtml(submission.id)}">Написать на почту</button>` : ""}
                                ${imNumber ? `<button class="action-button" type="button" data-change-im-number data-current-im-number="${escapeHtml(imNumber)}" data-default-im-number="${escapeHtml(imNumber)}" data-order-id="${escapeHtml(orderId)}" data-submission-id="${escapeHtml(submission.id)}">Изменить Номер ИМ</button>` : ""}
                            </div>`
                                : ""
                        }
                        ${renderCustomerSection(submission, rows)}
                        ${renderPaymentSection(rows)}
                        <section class="pretty-section wide"><h3>Товары</h3>${renderProductsTable(rows)}</section>
                        ${renderExtraFields(submission.payload)}
                        ${renderEmailHistory(submission)}
                        <details class="details wide"><summary>Технические данные</summary><pre>${escapeHtml(raw)}</pre></details>
                    </div>
                </details>
            </article>`;
    }

    function updateFilterOptions() {
        const selectedSize = sizeFilter.value;
        const selectedProductType = productTypeFilter.value;
        const selectedColor = colorFilter.value;
        const selectedSocial = socialFilter.value;
        const sizes = [
            ...new Set(orderRows.map((row) => row.size).filter(Boolean)),
        ].sort();
        const productTypes = [
            ...new Set(orderRows.map((row) => row.productType).filter(Boolean)),
        ].sort((a, b) => a.localeCompare(b));
        const colors = [
            ...new Map(
                orderRows.map((row) => [row.color, row.colorLabel]),
            ).entries(),
        ]
            .filter(([key]) => key)
            .sort((a, b) => a[1].localeCompare(b[1]));
        const socialNetworks = [
            ...new Map(
                orderRows.map((row) => [
                    row.socialNetwork,
                    {
                        label: row.socialNetworkLabel,
                        icon: row.socialNetworkIcon,
                    },
                ]),
            ).entries(),
        ]
            .filter(([key]) => key)
            .sort((a, b) => a[1].label.localeCompare(b[1].label));
        sizeFilter.innerHTML =
            '<option value="all">Все размеры</option>' +
            sizes
                .map(
                    (size) =>
                        `<option value="${escapeHtml(size)}">${escapeHtml(size)}</option>`,
                )
                .join("");
        productTypeFilter.innerHTML =
            '<option value="all">Все типы</option>' +
            productTypes
                .map(
                    (type) =>
                        `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`,
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
        socialFilter.innerHTML =
            '<option value="all">Все соц. сети</option>' +
            socialNetworks
                .map(
                    ([key, item]) =>
                        `<option value="${escapeHtml(key)}" data-icon="${escapeHtml(item.icon)}">${escapeHtml(item.label)}</option>`,
                )
                .join("");
        if (sizes.includes(selectedSize)) sizeFilter.value = selectedSize;
        if (productTypes.includes(selectedProductType))
            productTypeFilter.value = selectedProductType;
        if (colors.some(([key]) => key === selectedColor))
            colorFilter.value = selectedColor;
        if (socialNetworks.some(([key]) => key === selectedSocial))
            socialFilter.value = selectedSocial;
        document.dispatchEvent(
            new CustomEvent("tilda:filters-options-updated"),
        );
    }

    function resetFilters() {
        if (search) search.value = "";
        if (dateFrom) dateFrom.value = "";
        if (dateTo) dateTo.value = "";
        if (deliveryFilter) deliveryFilter.value = "all";
        if (sizeFilter) sizeFilter.value = "all";
        if (productTypeFilter) productTypeFilter.value = "all";
        if (colorFilter) colorFilter.value = "all";
        if (socialFilter) socialFilter.value = "all";
        document.dispatchEvent(new CustomEvent("tilda:filters-reset"));
        currentPage = 1;
        render();
    }

    function renderPagination(totalItems) {
        const totalPages =
            pageSize === "all"
                ? 1
                : Math.max(1, Math.ceil(totalItems / pageSize));
        currentPage = Math.min(currentPage, totalPages);
        const pageButtons = Array.from({ length: totalPages }, (_, index) => {
            const page = index + 1;
            return `<button class="pagination-page${page === currentPage ? " is-active" : ""}" type="button" data-page="${page}"${page === currentPage ? ' aria-current="page"' : ""}>${page}</button>`;
        }).join("");
        const markup = `
            <div class="pagination-pages" aria-label="Страницы">
                ${pageButtons}
            </div>
            <label class="pagination-size custom-filter" data-custom-select-filter>
                <span>Показывать</span>
                <select data-page-size data-custom-select aria-label="Количество заявок на странице">
                    ${[10, 20, 30, 50].map((size) => `<option value="${size}"${pageSize === size ? " selected" : ""}>${size}</option>`).join("")}
                    <option value="all"${pageSize === "all" ? " selected" : ""}>Все</option>
                </select>
            </label>`;
        paginationContainers.forEach((container) => {
            container.innerHTML = markup;
        });
        document.dispatchEvent(
            new CustomEvent("tilda:custom-filters-init", {
                detail: { root: root },
            }),
        );
    }

    function render() {
        visibleRows = orderRows.filter(matchesFilters);
        const visibleSubmissionIds = new Set(
            visibleRows.map((row) => row.submissionId),
        );
        const visibleSubmissions = submissions.filter((submission) =>
            visibleSubmissionIds.has(submission.id),
        );
        renderPagination(visibleSubmissions.length);
        const start = pageSize === "all" ? 0 : (currentPage - 1) * pageSize;
        const paginatedSubmissions =
            pageSize === "all"
                ? visibleSubmissions
                : visibleSubmissions.slice(start, start + pageSize);
        renderStats(visibleRows);
        visibleCount.textContent = `${fmtInt(visibleSubmissions.length)} заявок / ${fmtInt(visibleRows.length)} строк товаров`;
        status.textContent = `Показано: ${fmtInt(visibleSubmissions.length)} заявок, ${fmtInt(visibleRows.length)} товарных строк`;
        list.innerHTML = paginatedSubmissions.length
            ? paginatedSubmissions.map(renderSubmission).join("")
            : '<div class="empty-state">Заявок пока нет или ничего не найдено.</div>';
    }

    function renderLoadingSkeleton() {
        list.innerHTML = Array.from({ length: 3 })
            .map(
                () =>
                    '<div class="skeleton" style="height:76px" aria-hidden="true"></div>',
            )
            .join("");
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
            "productType",
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
        // Служебное поле _search в экспорт не попадает
        const cleanRows = visibleRows.map(({ _search, ...row }) => row);
        download(
            `${siteName}_orders.json`,
            JSON.stringify(cleanRows, null, 2),
            "application/json;charset=utf-8",
        );
    }

    async function loadSubmissions() {
        status.textContent = "Загружаем заявки...";
        refresh.textContent = refresh.dataset.loadingLabel || "Загрузка";
        refresh.disabled = true;
        renderLoadingSkeleton();
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
            refresh.textContent = refresh.dataset.idleLabel || "Обновить";
            refresh.disabled = false;
        }
    }

    const renderFromFirstPage = () => {
        currentPage = 1;
        render();
    };
    const debouncedRender = debounce(renderFromFirstPage, 200);
    // Текстовый поиск — с debounce, остальные фильтры — мгновенно
    search?.addEventListener("input", debouncedRender);
    [
        dateFrom,
        dateTo,
        deliveryFilter,
        productTypeFilter,
        sizeFilter,
        colorFilter,
        socialFilter,
    ].forEach((element) => {
        element?.addEventListener("change", renderFromFirstPage);
    });
    paginationContainers.forEach((container) => {
        container.addEventListener("click", (event) => {
            const pageButton = event.target.closest("[data-page]");
            if (!pageButton) return;
            currentPage = Number(pageButton.dataset.page);
            render();
            document.querySelector('[data-pagination="top"]')?.scrollIntoView({
                behavior: "smooth",
                block: "start",
            });
        });
        container.addEventListener("change", (event) => {
            if (!event.target.matches("[data-page-size]")) return;
            pageSize =
                event.target.value === "all"
                    ? "all"
                    : Number(event.target.value);
            currentPage = 1;
            render();
        });
    });
    refresh?.addEventListener("click", loadSubmissions);
    list?.addEventListener("click", (event) => {
        const actionButton = event.target.closest(".action-button");
        if (!actionButton) return;
        event.stopPropagation();
        event.preventDefault();
        if (actionButton.matches("[data-email-modal]")) {
            openEmailModal(actionButton);
            return;
        }
        if (actionButton.matches("[data-change-im-number]")) {
            changeCdekImNumber(actionButton);
        }
    });
    exportCsv?.addEventListener("click", exportVisibleCsv);
    exportJson?.addEventListener("click", exportVisibleJson);
    resetFiltersButton?.addEventListener("click", resetFilters);
    loadSubmissions();
})();
