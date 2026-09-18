"use strict";

const state = {
  csrfToken: "",
  groups: [],
  updates: new Map(),
  deletes: new Set(),
  activeGroup: "news",
};

const form = document.querySelector("#config-form");
const groupsRoot = document.querySelector("#config-groups");
const tabsRoot = document.querySelector("#tabs");
const notice = document.querySelector("#notice");
const saveButton = document.querySelector("#save-button");
const changeCount = document.querySelector("#change-count");

function escapeText(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function escapeAttribute(value) {
  return escapeText(value).replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function showNotice(message, kind = "success") {
  notice.textContent = message;
  notice.className = `notice visible ${kind}`;
  window.clearTimeout(showNotice.timer);
  showNotice.timer = window.setTimeout(() => {
    notice.className = "notice";
  }, 4500);
}

function renderSummary(data) {
  document.querySelector("#configured-count").textContent = `${data.configuredSecrets}/${data.totalSecrets}`;
  document.querySelector("#env-path").textContent = `保存位置：${data.envPath}`;
  document.querySelector("#keyless-sources").innerHTML = data.keylessSources
    .map((source) => `<span><i>✓</i>${escapeText(source)}</span>`)
    .join("");
}

function renderTabs() {
  tabsRoot.innerHTML = state.groups.map((group, index) => `
    <button type="button" class="tab ${group.id === state.activeGroup ? "active" : ""}" data-group="${group.id}">
      <b>0${index + 1}</b>${escapeText(group.title)}
    </button>`).join("");
}

function inputMarkup(field) {
  const common = `id="${field.name}" name="${field.name}" data-secret="${field.secret}"`;
  if (field.inputType === "select") {
    const empty = `<option value="">使用项目默认值</option>`;
    const options = field.options.map((option) =>
      `<option value="${escapeAttribute(option.value)}" ${field.value === option.value ? "selected" : ""}>${escapeText(option.label)}</option>`
    ).join("");
    return `<select ${common}>${empty}${options}</select>`;
  }
  const value = field.secret ? "" : field.value;
  const numeric = field.inputType === "number"
    ? `min="${field.min}" max="${field.max}" step="${field.step}"`
    : "";
  const placeholder = field.secret && field.configured
    ? "已安全保存；输入新值可替换"
    : (field.placeholder || "尚未配置");
  const autocomplete = field.secret ? "new-password" : "off";
  return `<input ${common} type="${field.inputType}" value="${escapeAttribute(value)}" placeholder="${escapeAttribute(placeholder)}" autocomplete="${autocomplete}" spellcheck="false" ${numeric}>`;
}

function fieldMarkup(field) {
  const status = field.configured
    ? `<span class="status configured"><i></i>已配置</span>`
    : `<span class="status"><i></i>未配置</span>`;
  const toggle = field.secret
    ? `<button class="icon-button reveal" type="button" aria-label="显示或隐藏输入内容" title="显示或隐藏">◉</button>`
    : "";
  const clear = field.configured
    ? `<button class="clear-button" type="button" data-clear="${field.name}">清除</button>`
    : "";
  return `
    <div class="field" data-field="${field.name}">
      <div class="field-heading">
        <label for="${field.name}">${escapeText(field.label)}</label>
        ${status}
      </div>
      <div class="input-row">${inputMarkup(field)}${toggle}${clear}</div>
      <div class="field-meta"><code>${field.name}</code>${field.description ? `<p>${escapeText(field.description)}</p>` : ""}</div>
    </div>`;
}

function renderGroups() {
  groupsRoot.innerHTML = state.groups.map((group) => `
    <section class="config-panel ${group.id === state.activeGroup ? "active" : ""}" data-panel="${group.id}">
      <div class="panel-heading"><div><p>CONFIG GROUP</p><h2>${escapeText(group.title)}</h2></div><span>${escapeText(group.description)}</span></div>
      <div class="field-grid">${group.fields.map(fieldMarkup).join("")}</div>
    </section>`).join("");
}

function updateDirtyState() {
  const count = state.updates.size + state.deletes.size;
  saveButton.disabled = count === 0;
  changeCount.textContent = count ? `${count} 项待保存` : "尚无变更";
}

function handleInput(event) {
  const input = event.target.closest("input, select");
  if (!input || !input.name) return;
  const isSecret = input.dataset.secret === "true";
  state.deletes.delete(input.name);
  const original = findField(input.name)?.value || "";
  if ((isSecret && input.value === "") || (!isSecret && input.value === original)) {
    state.updates.delete(input.name);
  } else if (!isSecret && input.value === "") {
    state.updates.delete(input.name);
    if (original) state.deletes.add(input.name);
  } else {
    state.updates.set(input.name, input.value);
  }
  const field = input.closest(".field");
  field?.classList.remove("will-clear");
  field?.classList.toggle("dirty", state.updates.has(input.name) || state.deletes.has(input.name));
  updateDirtyState();
}

function findField(name) {
  for (const group of state.groups) {
    const found = group.fields.find((field) => field.name === name);
    if (found) return found;
  }
  return null;
}

tabsRoot.addEventListener("click", (event) => {
  const button = event.target.closest("[data-group]");
  if (!button) return;
  state.activeGroup = button.dataset.group;
  renderTabs();
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === state.activeGroup);
  });
});

groupsRoot.addEventListener("input", handleInput);
groupsRoot.addEventListener("change", handleInput);
groupsRoot.addEventListener("click", (event) => {
  const reveal = event.target.closest(".reveal");
  if (reveal) {
    const input = reveal.parentElement.querySelector("input");
    input.type = input.type === "password" ? "text" : "password";
    reveal.classList.toggle("active", input.type === "text");
    return;
  }
  const clear = event.target.closest("[data-clear]");
  if (!clear) return;
  const name = clear.dataset.clear;
  const input = document.querySelector(`[name="${name}"]`);
  input.value = "";
  state.updates.delete(name);
  state.deletes.add(name);
  input.closest(".field").classList.add("dirty", "will-clear");
  updateDirtyState();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.updates.size && !state.deletes.size) return;
  saveButton.disabled = true;
  saveButton.classList.add("saving");
  try {
    const response = await fetch("/api/config", {
      method: "PUT",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": state.csrfToken},
      body: JSON.stringify({updates: Object.fromEntries(state.updates), deletes: [...state.deletes]}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "保存失败。");
    state.csrfToken = data.csrfToken;
    state.groups = data.groups;
    state.updates.clear();
    state.deletes.clear();
    renderSummary(data);
    renderTabs();
    renderGroups();
    updateDirtyState();
    showNotice(data.message);
  } catch (error) {
    showNotice(error.message || "无法保存配置。", "error");
    updateDirtyState();
  } finally {
    saveButton.classList.remove("saving");
  }
});

async function loadConfig() {
  try {
    const response = await fetch("/api/config", {headers: {"Accept": "application/json"}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "读取配置失败。");
    state.csrfToken = data.csrfToken;
    state.groups = data.groups;
    renderSummary(data);
    renderTabs();
    renderGroups();
  } catch (error) {
    groupsRoot.innerHTML = `<div class="load-error"><strong>无法读取配置</strong><p>${escapeText(error.message)}</p></div>`;
  }
}

loadConfig();
