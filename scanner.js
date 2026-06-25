const form = document.querySelector("#scan-form");
const scanButton = document.querySelector("#scan-button");
const publishButton = document.querySelector("#publish-button");
const statusBox = document.querySelector("#status");
const fileCount = document.querySelector("#file-count");
const packageCount = document.querySelector("#package-count");
const endpointCount = document.querySelector("#endpoint-count");
const fileList = document.querySelector("#file-list");
const endpointList = document.querySelector("#endpoint-list");
const openApiJson = document.querySelector("#openapi-json");
const downloadJson = document.querySelector("#download-json");
const tabs = document.querySelectorAll(".tab");
const scanConfirmModalElement = document.querySelector("#scan-confirm-modal");
const scanConfirmModal = new bootstrap.Modal(scanConfirmModalElement);
const scanConfirmName = document.querySelector("#scan-confirm-name");
const confirmScanButton = document.querySelector("#confirm-scan-button");
const authMode = document.querySelector("#auth-mode");
const oauthTokenUrlField = document.querySelector("#oauth-token-url-field");
const oauthTokenUrl = document.querySelector("#oauth-token-url");
let swaggerUrl = "/generated/openapi.json";
let instances = [];
let activeSlug = "";
let pendingScanPayload = null;

function setStatus(message, isError = false) {
  statusBox.textContent = message;
  statusBox.className = `alert mb-3 ${isError ? "alert-danger" : "alert-secondary"}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderFiles(files) {
  if (!files.length) {
    fileList.innerHTML = '<div class="list-group-item text-secondary small">No .cs files were found.</div>';
    return;
  }

  fileList.innerHTML = files
    .slice(0, 200)
    .map(
      (file) => `
        <div class="list-group-item">
          <strong class="d-block">${escapeHtml(file.name)}</strong>
          <small class="text-secondary">${escapeHtml(file.relativePath)}</small>
        </div>
      `
    )
    .join("");
}

function formatDate(value) {
  if (!value) {
    return "never scanned";
  }
  return new Date(value).toLocaleString("en-US");
}

function normalizeBaseUrl(value) {
  return String(value || "")
    .trim()
    .replace(/\/+$/, "")
    .replace(/\/0$/i, "") || "https://your-creatio-site.com";
}

function updateAuthFields() {
  const oauth = authMode.value === "oauth";
  oauthTokenUrlField.classList.toggle("d-none", !oauth);
  oauthTokenUrl.required = oauth;
  if (!oauth) oauthTokenUrl.setCustomValidity("");
}

function fillForm(item) {
  document.querySelector("#doc-name").value = item?.name || "";
  document.querySelector("#project-path").value = item?.projectPath || "";
  document.querySelector("#base-url").value = normalizeBaseUrl(item?.baseUrl);
  document.querySelector("#package-prefix").value = item?.packagePrefix || "";
  authMode.value = item?.authMode || "bpmcsrf";
  oauthTokenUrl.value = item?.oauthTokenUrl || "";
  updateAuthFields();
}

async function selectInstance(slug) {
  const item = instances.find((entry) => entry.slug === slug);
  if (!item) {
    return;
  }
  activeSlug = slug;
  fillForm(item);
  swaggerUrl = `/api/instances/${encodeURIComponent(slug)}/openapi`;
  downloadJson.href = swaggerUrl;
  await loadStoredResult(slug, item);
}

async function loadStoredResult(slug, item) {
  try {
    const response = await fetch(
      `/api/instances/${encodeURIComponent(slug)}/scan-result`,
      { cache: "no-store" }
    );
    if (!response.ok) {
      throw new Error("Previous scan details are unavailable. Run the scan again.");
    }
    const result = await response.json();
    if (!Array.isArray(result.files) || !Array.isArray(result.endpoints)) {
      throw new Error("Previous scan details are incomplete. Run the scan again.");
    }
    renderResult(result);
    await loadSwagger();
    setStatus(
      `Scan results from ${formatDate(item.generatedAt)} were restored.`
    );
  } catch (error) {
    fileCount.textContent = item.fileCount || 0;
    packageCount.textContent = item.packageCount || 0;
    endpointCount.textContent = item.endpointCount || 0;
    renderFiles([]);
    renderEndpoints([]);
    await loadSwagger();
    setStatus(error.message, true);
  }
}

async function loadInstances() {
  try {
    const response = await fetch("/api/instances", { cache: "no-store" });
    const catalog = await response.json();
    instances = catalog.items || [];
    const requested = new URLSearchParams(window.location.search).get("doc");
    if (requested) {
      await selectInstance(requested);
    }
  } catch (error) {
    setStatus("The local instance database could not be read.", true);
  }
}

function renderEndpoints(endpoints) {
  if (!endpoints.length) {
    endpointList.innerHTML =
      '<div class="list-group-item text-secondary">No endpoints were detected. Ensure the service uses OperationContract, WebInvoke, or WebGet.</div>';
    return;
  }

  endpointList.innerHTML = endpoints
    .map(
      (endpoint) => `
        <article class="list-group-item endpoint-item">
          <span class="method-badge ${escapeHtml(endpoint.method.toLowerCase())}">
            ${escapeHtml(endpoint.method)}
          </span>
          <div>
            <strong class="d-block endpoint-path">${escapeHtml(endpoint.path)}</strong>
            <small class="text-secondary">${escapeHtml(endpoint.className)}.${escapeHtml(endpoint.methodName)} - ${escapeHtml(endpoint.sourceFile)}</small>
          </div>
        </article>
      `
    )
    .join("");
}

async function loadSwagger() {
  if (!window.SwaggerUIBundle) {
    setStatus("The Swagger UI CDN has not loaded. Endpoints are still available in the JSON tab.", true);
    return;
  }

  try {
    const response = await fetch(swaggerUrl, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`The local OpenAPI document is unavailable (${response.status}).`);
    }
    const spec = await response.json();
    if (!spec.openapi || !spec.paths) {
      throw new Error("The local OpenAPI response is invalid.");
    }
    const oauthFlow =
      spec.components?.securitySchemes?.oauth2ClientCredentials?.flows
        ?.clientCredentials;
    const configuredTokenUrl = oauthFlow?.tokenUrl || "";
    const oauthProxyUrl = activeSlug
      ? `${window.location.origin}/api/oauth/token/${encodeURIComponent(activeSlug)}`
      : "";
    SwaggerUIBundle({
      spec,
      dom_id: "#swagger-ui",
      deepLinking: true,
      persistAuthorization: true,
      displayRequestDuration: true,
      defaultModelsExpandDepth: -1,
      tryItOutEnabled: true,
      requestInterceptor: (request) => {
        if (configuredTokenUrl && request.url === configuredTokenUrl && oauthProxyUrl) {
          request.url = oauthProxyUrl;
        }
        return request;
      }
    });
  } catch (error) {
    document.querySelector("#swagger-ui").innerHTML = "";
    setStatus(`${error.message} Run the scan again.`, true);
  }
}

function renderResult(result) {
  const files = result.files || [];
  const packages = result.packages || [];
  const endpoints = result.endpoints || [];
  fileCount.textContent = files.length;
  packageCount.textContent = packages.length;
  endpointCount.textContent = endpoints.length;
  renderFiles(files);
  renderEndpoints(endpoints);
  openApiJson.textContent = JSON.stringify(result.openapi, null, 2);
  swaggerUrl = result.localOpenApiUrl || (
    result.catalogItem?.slug
      ? `/api/instances/${encodeURIComponent(result.catalogItem.slug)}/openapi`
      : "/generated/openapi.json"
  );
  downloadJson.href = swaggerUrl;
  activeSlug = result.catalogItem?.slug || activeSlug;
}

function scanPayload() {
  return {
    docName: document.querySelector("#doc-name").value.trim(),
    path: document.querySelector("#project-path").value.trim(),
    baseUrl: document.querySelector("#base-url").value.trim(),
    packagePrefix: document.querySelector("#package-prefix").value.trim(),
    authMode: authMode.value,
    oauthTokenUrl: oauthTokenUrl.value.trim()
  };
}

function requestScan(event) {
  event.preventDefault();
  const payload = scanPayload();
  const current = instances.find((item) => item.slug === activeSlug);
  if (current?.generatedAt) {
    pendingScanPayload = payload;
    scanConfirmName.textContent = current.name;
    scanConfirmModal.show();
    return;
  }
  executeScan(payload);
}

function changeMessage(summary) {
  if (!summary || summary.initialScan) {
    return "Scan completed. C# file details, endpoints, and the OpenAPI document were saved.";
  }
  if (!summary.hasChanges) {
    return "Scan completed. No changes or additions were found.";
  }
  return [
    "Scan completed.",
    `${summary.addedFiles.length} new files`,
    `${summary.modifiedFiles.length} modified files`,
    `${summary.removedFiles.length} deleted files`,
    `${summary.addedEndpointCount} new endpoints`,
    `${summary.removedEndpointCount} deleted endpoints`,
    summary.authenticationChanged ? "authentication changed" : ""
  ].filter(Boolean).join(" ");
}

async function executeScan(payload) {
  scanButton.disabled = true;
  setStatus("Scanning the Creatio project...");

  try {
    const response = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || "Scan failed.");
    }

    renderResult(result);
    const url = new URL(window.location.href);
    url.searchParams.set("doc", result.catalogItem.slug);
    window.history.replaceState({}, "", url);
    activeSlug = result.catalogItem.slug;
    const catalogResponse = await fetch("/api/instances", { cache: "no-store" });
    const catalog = await catalogResponse.json();
    instances = catalog.items || [];
    await loadSwagger();
    setStatus(changeMessage(result.changeSummary));
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    scanButton.disabled = false;
  }
}

confirmScanButton.addEventListener("click", () => {
  if (!pendingScanPayload) return;
  const payload = pendingScanPayload;
  pendingScanPayload = null;
  scanConfirmModal.hide();
  executeScan(payload);
});

scanConfirmModalElement.addEventListener("hidden.bs.modal", () => {
  pendingScanPayload = null;
  scanConfirmName.textContent = "";
});

async function publishCurrent() {
  const slug = activeSlug || new URLSearchParams(window.location.search).get("doc");
  if (!slug) {
    setStatus("Scan or select an instance before publishing.", true);
    return;
  }

  publishButton.disabled = true;
  setStatus("Publishing to the Viewer service...");

  const payload = { slug };

  try {
    const response = await fetch("/api/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Publish failed.");
    }
    setStatus(`Publish successful: ${result.result.item.publishedUrl}`);
  } catch (error) {
    setStatus(`${error.message} Check the configuration in Settings.`, true);
  } finally {
    publishButton.disabled = false;
  }
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((item) => item.classList.remove("active"));
    document
      .querySelectorAll(".result-panel > .view")
      .forEach((view) => view.classList.remove("active"));
    tab.classList.add("active");
    document.querySelector(`#view-${tab.dataset.view}`).classList.add("active");
  });
});

form.addEventListener("submit", requestScan);
authMode.addEventListener("change", updateAuthFields);
publishButton.addEventListener("click", publishCurrent);
loadInstances().then(() => {
  if (!activeSlug) loadSwagger();
});
updateAuthFields();

// ── Folder browser ────────────────────────────────────────────────────────
const browseModalEl = document.querySelector("#browse-modal");
const browseModal = new bootstrap.Modal(browseModalEl);
const browseList = document.querySelector("#browse-list");
const browseEmpty = document.querySelector("#browse-empty");
const browseError = document.querySelector("#browse-error");
const browseBreadcrumb = document.querySelector("#browse-breadcrumb");
const browseSelectedLabel = document.querySelector("#browse-selected-label");
const btnBrowseSelect = document.querySelector("#btn-browse-select");
const projectPathInput = document.querySelector("#project-path");
let browseCurrentPath = "";
let browseSelectedPath = null;

async function browseDir(relPath) {
  browseList.innerHTML = "";
  browseEmpty.classList.add("d-none");
  browseError.classList.add("d-none");
  browseList.innerHTML = '<div class="list-group-item text-secondary py-3 text-center"><span class="spinner-border spinner-border-sm me-2"></span>Memuat...</div>';

  const url = "/api/browse" + (relPath ? "?path=" + encodeURIComponent(relPath) : "");
  let data;
  try {
    const res = await fetch(url);
    data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
  } catch (err) {
    browseList.innerHTML = "";
    browseError.textContent = "Gagal memuat direktori: " + err.message;
    browseError.classList.remove("d-none");
    return;
  }

  browseCurrentPath = data.current;
  browseSelectedPath = data.fullPath;
  browseSelectedLabel.textContent = data.hostPath || data.fullPath;
  btnBrowseSelect.disabled = data.current === "";


  // breadcrumb
  const parts = data.current ? data.current.split("/") : [];
  let crumbs = '<span class="me-1">/creatio</span>';
  let accumulated = "";
  parts.filter(Boolean).forEach((p, i) => {
    accumulated += (accumulated ? "/" : "") + p;
    const snap = accumulated;
    crumbs += `<i class="bi bi-chevron-right mx-1 opacity-50" aria-hidden="true"></i>`;
    if (i === parts.length - 1) {
      crumbs += `<strong>${p}</strong>`;
    } else {
      crumbs += `<a href="#" class="browse-crumb text-decoration-none" data-path="${snap}">${p}</a>`;
    }
  });
  browseBreadcrumb.innerHTML = crumbs;
  browseBreadcrumb.querySelectorAll(".browse-crumb").forEach(a => {
    a.addEventListener("click", e => { e.preventDefault(); browseDir(a.dataset.path); });
  });

  browseList.innerHTML = "";

  if (data.parent !== null && data.parent !== undefined) {
    const up = document.createElement("button");
    up.type = "button";
    up.className = "list-group-item list-group-item-action d-flex align-items-center gap-2 py-2";
    up.innerHTML = '<i class="bi bi-arrow-up text-secondary" aria-hidden="true"></i><span class="text-secondary">..</span>';
    up.addEventListener("click", () => browseDir(data.parent || ""));
    browseList.appendChild(up);
  }

  if (data.entries.length === 0 && data.parent === null) {
    browseEmpty.classList.remove("d-none");
    return;
  }

  data.entries.forEach(name => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "list-group-item list-group-item-action d-flex align-items-center gap-2 py-2";
    const childPath = data.current ? data.current + "/" + name : name;
    btn.innerHTML = `<i class="bi bi-folder-fill text-warning" aria-hidden="true"></i>${name}`;
    btn.addEventListener("click", () => browseDir(childPath));
    browseList.appendChild(btn);
  });

  if (data.entries.length === 0) browseEmpty.classList.remove("d-none");
}

document.querySelector("#btn-browse").addEventListener("click", () => {
  browseSelectedPath = null;
  btnBrowseSelect.disabled = true;
  browseSelectedLabel.textContent = "";
  browseDir("");
  browseModal.show();
});

btnBrowseSelect.addEventListener("click", () => {
  if (browseSelectedPath) projectPathInput.value = browseSelectedPath;
  browseModal.hide();
});

