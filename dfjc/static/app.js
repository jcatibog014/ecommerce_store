// Extras only: every page works without this file.

// The Appearance choice on the Settings page. theme.js applies it on every page.
function setUpTheme() {
  const section = document.querySelector("[data-theme-settings]");
  if (!section) return;
  const root = document.documentElement;
  section.hidden = false;
  section.querySelectorAll('input[name="theme"]').forEach((choice) => {
    choice.checked = choice.value === (root.dataset.theme || "system");
    choice.addEventListener("change", () => {
      try {
        if (choice.value === "system") {
          delete root.dataset.theme;
          localStorage.removeItem("dfjc-theme");
        } else {
          root.dataset.theme = choice.value;
          localStorage.setItem("dfjc-theme", choice.value);
        }
      } catch (error) {
        // Storage can be blocked. The theme still changes for this page.
      }
    });
  });
}

function setUpMenu() {
  const button = document.querySelector("[data-menu-button]");
  const menu = document.getElementById(button && button.getAttribute("aria-controls"));
  if (!button || !menu) return;
  button.hidden = false;
  const setOpen = (open) => {
    button.setAttribute("aria-expanded", open);
    menu.classList.toggle("is-open", open);
  };
  button.addEventListener("click", () => setOpen(button.getAttribute("aria-expanded") !== "true"));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setOpen(false);
  });
}

function setUpUserMenu() {
  document.querySelectorAll("[data-user-menu]").forEach((menu) => {
    document.addEventListener("click", (event) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && menu.open) {
        menu.open = false;
        menu.querySelector("summary").focus();
      }
    });
  });
}

// "Are you sure?" before removing something: on a whole form, or on one of its buttons.
function setUpConfirm() {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
  document.querySelectorAll("button[data-confirm-button]").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (!confirm(button.dataset.confirmButton)) event.preventDefault();
    });
  });
}

// Messages float as toasts. Good news fades after 5 seconds (not while pointed at or
// focused); a problem stays until closed, so nobody misses it.
function setUpToasts() {
  document.querySelectorAll("[data-flash]").forEach((toast) => {
    const close = toast.querySelector("[data-flash-close]");
    close.hidden = false;
    const leave = () => {
      toast.classList.add("is-leaving");
      setTimeout(() => toast.remove(), 300);
    };
    close.addEventListener("click", leave);
    if ("stays" in toast.dataset) return;
    let timer = setTimeout(leave, 5000);
    const pause = () => clearTimeout(timer);
    const resume = () => { clearTimeout(timer); timer = setTimeout(leave, 3000); };
    toast.addEventListener("mouseenter", pause);
    toast.addEventListener("focusin", pause);
    toast.addEventListener("mouseleave", resume);
    toast.addEventListener("focusout", resume);
  });
}

// "Copy link" beside an invite link. Where the browser has no clipboard (an http
// address), the text is selected for Ctrl+C instead.
function setUpCopy() {
  document.querySelectorAll("[data-copy-button]").forEach((button) => {
    const source = button.closest(".card, .setting").querySelector("[data-copy-text]");
    if (!source) return;
    if (!navigator.clipboard && !("copyAnywhere" in button.dataset)) return;
    button.hidden = false;
    const label = button.textContent;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(source.value || source.textContent.trim());
        button.textContent = "Copied";
      } catch (error) {
        if (source.select) source.select();
        button.textContent = "Selected: press Ctrl+C";
      }
      setTimeout(() => { button.textContent = label; }, 2500);
    });
  });
}

// Each Save button looks asleep until its form has a change, then wakes up and says
// so. It still works either way (and without JavaScript it is always awake).
function setUpUnsavedChanges() {
  document.querySelectorAll("form[data-watch-changes]").forEach((form) => {
    const button = form.querySelector("button[type=submit]");
    if (!button || button.closest("fieldset:disabled")) return;
    const note = document.createElement("span");
    note.className = "unsaved-note";
    note.textContent = "Unsaved changes";
    note.hidden = true;
    button.before(note);
    const snapshot = () => new URLSearchParams(new FormData(form)).toString();
    const start = snapshot();
    const update = () => {
      const changed = snapshot() !== start;
      button.classList.toggle("is-waiting", !changed);
      note.hidden = !changed;
    };
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    update();
  });
}

// Choosing a sort or a category shows the result straight away; typed words wait
// for the button. Empty fields stay out of the address, so it is short.
function setUpFilters() {
  document.querySelectorAll("[data-filters]").forEach((form) => {
    form.addEventListener("change", (event) => {
      if (event.target.matches("select")) form.requestSubmit();
    });
    form.addEventListener("submit", () => {
      form.querySelectorAll("input[name], select[name]").forEach((field) => {
        if (field.value === "") field.disabled = true;
      });
    });
  });
}

// The demo's View As: choosing a role switches straight away (the button is for no JavaScript).
function setUpAutoSubmit() {
  document.querySelectorAll("select[data-auto-submit]").forEach((select) => {
    const button = select.form.querySelector("[data-auto-submit-button]");
    if (button) button.hidden = true;
    select.addEventListener("change", () => select.form.requestSubmit());
  });
}

// A click anywhere on a table row or a product card opens its page (the name is
// the real link, for keyboards).
function setUpRowLinks() {
  document.querySelectorAll("tr[data-href]").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.closest("a, button, input, select, label, details")) return;
      if (String(getSelection())) return;  // selecting text to copy isn't a click to open
      location.href = row.dataset.href;
    });
  });
}

// Money arrives in centavos, like the server keeps it: 2050 -> "₱20.50".
function peso(centavos) {
  const sign = centavos < 0 ? "-" : "";
  const value = Math.abs(centavos);
  const whole = Math.floor(value / 100).toLocaleString("en-US");
  return `${sign}₱${whole}.${String(value % 100).padStart(2, "0")}`;
}

// Checkout: the address only shows for delivery, and the total follows the barangay's fee.
// The server works everything out again; this only helps the shopper see it.
function setUpCheckout() {
  const form = document.querySelector("[data-checkout]");
  if (!form) return;
  const summary = form.querySelector("[data-summary]");
  const subtotal = Number(summary.dataset.subtotal) - Number(summary.dataset.discount || 0);
  const freeDelivery = Boolean(summary.dataset.freeDelivery);
  const newFields = form.querySelector("[data-new-address-fields]");
  const update = () => {
    const how = (form.querySelector('input[name="fulfilment"]:checked') || {}).value || "pickup";
    form.querySelectorAll("[data-show-when]").forEach((part) => { part.hidden = part.dataset.showWhen !== how; });
    const address = form.querySelector('input[name="address_id"]:checked');
    const isNew = !address || "newAddress" in address.dataset;
    if (newFields) newFields.hidden = !isNew;
    let fee = null;
    if (how === "delivery") {
      if (isNew) {
        const option = form.querySelector("[data-area-select]");
        const chosen = option && option.selectedOptions[0];
        if (chosen && chosen.dataset.fee) fee = Number(chosen.dataset.fee);
      } else {
        fee = Number(address.dataset.fee);
      }
    }
    form.querySelector("[data-fee-text]").textContent = fee === null ? "Depends on the barangay" : peso(fee);
    const charged = how === "delivery" && fee && !freeDelivery ? fee : 0;
    form.querySelector("[data-total-text]").textContent = peso(subtotal + charged);
  };
  form.addEventListener("change", update);
  update();
}

// The cashier's Price This Order form: each line's amount and the total follow the
// Retail / Wholesale choice, and "Set all to" changes every line at once.
function setUpPriceForm() {
  const form = document.querySelector("[data-price-form]");
  if (!form) return;
  const fee = Number(form.dataset.deliveryFee);
  const update = () => {
    let subtotal = 0;
    form.querySelectorAll("[data-line]").forEach((row) => {
      const choice = row.querySelector("input[type=radio]:checked").value;
      const amount = Number(row.dataset.quantity) * Number(row.dataset[choice]);
      row.querySelector("[data-line-total]").textContent = peso(amount);
      row.classList.toggle("is-wholesale", choice === "wholesale");
      subtotal += amount;
    });
    form.querySelectorAll("[data-request]").forEach((row) => {
      const have = row.querySelector('input[type=radio][value="available"]').checked;
      const raw = row.querySelector("[data-request-price]").value.replace(/[,\s₱]/g, "");
      const ok = /^\d+(\.\d{1,2})?$/.test(raw);
      const [whole, cents = ""] = ok ? raw.split(".") : ["0"];
      const amount = have && ok ? Number(row.dataset.quantity) * (Number(whole) * 100 + Number((cents + "00").slice(0, 2))) : 0;
      row.querySelector("[data-line-total]").textContent = have && ok ? peso(amount) : "-";
      subtotal += amount;
    });
    form.querySelector("[data-subtotal-text]").textContent = peso(subtotal);
    form.querySelector("[data-total-text]").textContent = peso(subtotal + fee);
  };
  const all = form.querySelector("[data-price-all]");
  all.hidden = false;
  all.querySelectorAll("[data-set-all]").forEach((button) => {
    button.addEventListener("click", () => {
      form.querySelectorAll(`input[type=radio][value="${button.dataset.setAll}"]:not(:disabled)`)
        .forEach((radio) => { radio.checked = true; });
      update();
    });
  });
  form.addEventListener("change", update);
  form.addEventListener("input", update);
  // Typing a price means "we have it".
  form.querySelectorAll("[data-request-price]").forEach((input) => {
    input.addEventListener("input", () => {
      if (input.value) input.closest("[data-request]").querySelector('input[value="available"]').checked = true;
    });
  });
  update();
}

// The counter: the change shows as the cash given is typed (the server works it out again).
function setUpChange() {
  const form = document.querySelector("[data-change-form]");
  if (!form) return;
  const total = Number(form.dataset.total);
  const input = form.querySelector('input[name="cash_given"]');
  const line = form.querySelector("[data-change-text]");
  const update = () => {
    const raw = input.value.replace(/[,\s₱]/g, "");
    if (!/^\d+(\.\d{1,2})?$/.test(raw)) { line.hidden = true; return; }
    const [whole, cents = ""] = raw.split(".");
    const given = Number(whole) * 100 + Number((cents + "00").slice(0, 2));
    line.hidden = false;
    line.classList.toggle("is-short", given < total);
    line.textContent = given < total ? `Short by ${peso(total - given)}` : `Change: ${peso(given - total)}`;
  };
  input.addEventListener("input", update);
}

// "Print receipt" (the page's print styles show only the receipt).
function setUpPrint() {
  document.querySelectorAll("[data-print]").forEach((button) => {
    button.hidden = false;
    button.addEventListener("click", () => window.print());
  });
}

// Charts: a tooltip for each day's bar (mouse, finger or keyboard focus).
function setUpChartTips() {
  const tip = document.querySelector("[data-chart-tip]");
  if (!tip) return;
  const show = (target) => {
    const [title, ...lines] = target.dataset.tip.split("|");
    const heading = document.createElement("strong");
    heading.textContent = title;
    tip.replaceChildren(heading, ...lines.map((line) => {
      const row = document.createElement("span");
      row.textContent = line;
      row.style.display = "block";
      return row;
    }));
    tip.hidden = false;
    const box = target.getBoundingClientRect();
    const left = Math.min(window.innerWidth - tip.offsetWidth - 8, Math.max(8, box.left + box.width / 2 - tip.offsetWidth / 2));
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(8, box.top - tip.offsetHeight - 8)}px`;
  };
  document.querySelectorAll("[data-tip]").forEach((target) => {
    target.addEventListener("mouseenter", () => show(target));
    target.addEventListener("focus", () => show(target));
    target.addEventListener("mouseleave", () => { tip.hidden = true; });
    target.addEventListener("blur", () => { tip.hidden = true; });
  });
}

// The phone app: the service worker (/sw.js) makes the store installable. Browsers that can
// install it fire "beforeinstallprompt"; then the Install button in Settings shows.
function setUpInstall() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Without it the site still works; it just can't be installed.
    });
  }
  const section = document.querySelector("[data-install]");
  if (!section) return;
  const button = section.querySelector("[data-install-button]");
  const done = section.querySelector("[data-installed]");
  if (window.matchMedia("(display-mode: standalone)").matches) {
    done.hidden = false;
    return;
  }
  let offer = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    offer = event;
    button.hidden = false;
  });
  button.addEventListener("click", async () => {
    if (!offer) return;
    offer.prompt();
    await offer.userChoice;
    offer = null;
    button.hidden = true;
  });
  window.addEventListener("appinstalled", () => { button.hidden = true; done.hidden = false; });
}

// Packing's Send out: the button wakes up once a rider is chosen.
function setUpSendOut() {
  document.querySelectorAll(".send-form").forEach((form) => {
    const select = form.querySelector("select");
    const button = form.querySelector("button");
    if (!select || !button || button.disabled) return;  // no riders at all: it stays off
    const sync = () => { button.disabled = !select.value; };
    sync();
    select.addEventListener("change", sync);
  });
}

setUpTheme();
setUpSendOut();
setUpChartTips();
setUpInstall();
setUpChange();
setUpPrint();
setUpCheckout();
setUpPriceForm();
setUpToasts();
setUpMenu();
setUpUserMenu();
setUpConfirm();
setUpCopy();
setUpUnsavedChanges();
setUpFilters();
setUpAutoSubmit();
setUpRowLinks();
