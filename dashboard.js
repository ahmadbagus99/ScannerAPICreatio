const rows = document.querySelector("#instance-rows");
const emptyState = document.querySelector("#empty-state");
const meta = document.querySelector("#dashboard-meta");
const searchInput = document.querySelector("#search-input");
const syncButton = document.querySelector("#sync-button");
const syncStatus = document.querySelector("#sync-status");
const publishModalElement = document.querySelector("#publish-modal");
const publishModal = new bootstrap.Modal(publishModalElement);
const publishInstanceName = document.querySelector("#publish-instance-name");
const publishInstanceSlug = document.querySelector("#publish-instance-slug");
const publishConfirmation = document.querySelector("#publish-confirmation");
const publishResult = document.querySelector("#publish-result");
const confirmPublishButton = document.querySelector("#confirm-publish-button");
const publishCloseButton = document.querySelector("#publish-close-button");
const deleteModalElement = document.querySelector("#delete-modal");
const deleteModal = new bootstrap.Modal(deleteModalElement);
const deleteInstanceName = document.querySelector("#delete-instance-name");
const deleteInstanceSlug = document.querySelector("#delete-instance-slug");
const deleteViewerOption = document.querySelector("#delete-viewer-option");
const deleteRemoteCheckbox = document.querySelector("#delete-remote-checkbox");
const deleteConfirmation = document.querySelector("#delete-confirmation");
const deleteResult = document.querySelector("#delete-result");
const confirmDeleteButton = document.querySelector("#confirm-delete-button");
const deleteCloseButton = document.querySelector("#delete-close-button");

let instances = [];
let pendingPublishSlug = "";
let pendingDeleteSlug = "";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString("en-US") : "-";
}

function filteredInstances() {
  const query = searchInput.value.trim().toLowerCase();
  if (!query) return instances;
  return instances.filter((item) =>
    [item.name, item.slug, item.packagePrefix, item.baseUrl, item.projectPath]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(query))
  );
}

function viewerUrl(item) {
  return item.publishedUrl || "#";
}

function publishState(item) {
  if (item.publishStatus) return item.publishStatus;
  return item.publishedUrl || item.publishedAt ? "published" : "not_published";
}

function statusBadge(item) {
  const state = publishState(item);
  if (state === "published") return '<span class="badge text-bg-success">Published</span>';
  if (state === "removed") return '<span class="badge text-bg-danger">Removed</span>';
  return '<span class="badge text-bg-secondary">Not Published</span>';
}

function render() {
  const items = filteredInstances();
  meta.textContent = `${instances.length} registered instances`;
  emptyState.style.display = items.length ? "none" : "block";
  rows.innerHTML = items.map((item) => {
    const published = publishState(item) === "published";
    const publishAction = published
      ? ""
      : `
        <button class="btn btn-sm btn-outline-success" type="button" data-publish="${escapeHtml(item.slug)}" title="Publish to Viewer">
          <i class="bi bi-cloud-arrow-up"></i><span class="d-none d-xl-inline ms-1">Publish</span>
        </button>
      `;
    return `
    <tr>
      <td>
        <a class="table-name" href="/scanner.html?doc=${encodeURIComponent(item.slug)}">${escapeHtml(item.name)}</a>
        <small class="d-block text-secondary">${escapeHtml(item.baseUrl || "-")}</small>
      </td>
      <td>${escapeHtml(item.packagePrefix || "-")}</td>
      <td>${Number(item.endpointCount || 0)}</td>
      <td>${Number(item.packageCount || 0)}</td>
      <td>
        ${statusBadge(item)}
        <small class="d-block text-secondary">${item.lastSyncedAt ? `Synced ${escapeHtml(formatDate(item.lastSyncedAt))}` : "Not synced"}</small>
      </td>
      <td>${escapeHtml(formatDate(item.generatedAt))}</td>
      <td class="text-end">
        <div class="d-flex justify-content-end gap-2">
          ${publishAction}
          ${published ? `
            <a class="btn btn-sm btn-outline-secondary" href="${escapeHtml(viewerUrl(item))}" target="_blank" rel="noreferrer" title="Open Viewer">
              <i class="bi bi-eye"></i><span class="d-none d-xl-inline ms-1">Viewer</span>
            </a>
          ` : ""}
          <button class="btn btn-sm btn-outline-danger" type="button" data-delete="${escapeHtml(item.slug)}" title="Delete local instance">
            <i class="bi bi-trash3"></i><span class="d-none d-xl-inline ms-1">Delete</span>
          </button>
        </div>
      </td>
    </tr>
  `;
  }).join("");
}

async function loadInstances() {
  const response = await fetch("/api/instances", { cache: "no-store" });
  if (!response.ok) throw new Error("The instance database is unavailable.");
  const catalog = await response.json();
  instances = catalog.items || [];
  render();
}

function openPublishModal(slug) {
  const item = instances.find((entry) => entry.slug === slug);
  if (!item) return;
  pendingPublishSlug = slug;
  publishInstanceName.textContent = item.name;
  publishInstanceSlug.textContent = item.slug;
  publishConfirmation.classList.remove("d-none");
  publishResult.className = "alert d-none mb-0";
  publishResult.textContent = "";
  confirmPublishButton.classList.remove("d-none");
  confirmPublishButton.disabled = false;
  confirmPublishButton.innerHTML = '<i class="bi bi-cloud-arrow-up me-1" aria-hidden="true"></i>Publish';
  publishCloseButton.textContent = "Cancel";
  publishModal.show();
}

async function confirmPublish() {
  if (!pendingPublishSlug) return;
  confirmPublishButton.disabled = true;
  confirmPublishButton.innerHTML =
    '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Publishing...';
  publishCloseButton.disabled = true;
  try {
    const response = await fetch("/api/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug: pendingPublishSlug })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Publish failed.");
    instances = result.catalog.items || [];
    render();
    syncStatus.textContent = `Publish successful: ${result.result.item.publishedUrl}`;
    publishConfirmation.classList.add("d-none");
    publishResult.className = "alert alert-success mb-0";
    publishResult.textContent = "Documentation was published to Viewer successfully.";
    confirmPublishButton.classList.add("d-none");
    publishCloseButton.textContent = "Close";
  } catch (error) {
    publishConfirmation.classList.add("d-none");
    publishResult.className = "alert alert-danger mb-0";
    publishResult.textContent = `${error.message} Check the configuration in Settings.`;
    confirmPublishButton.classList.add("d-none");
    publishCloseButton.textContent = "Close";
  } finally {
    confirmPublishButton.disabled = false;
    publishCloseButton.disabled = false;
  }
}

function openDeleteModal(slug) {
  const item = instances.find((entry) => entry.slug === slug);
  if (!item) return;
  pendingDeleteSlug = slug;
  deleteInstanceName.textContent = item.name;
  deleteInstanceSlug.textContent = item.slug;
  deleteViewerOption.classList.toggle("d-none", publishState(item) !== "published");
  deleteRemoteCheckbox.checked = false;
  deleteConfirmation.classList.remove("d-none");
  deleteResult.className = "alert d-none mb-0";
  deleteResult.textContent = "";
  confirmDeleteButton.classList.remove("d-none");
  confirmDeleteButton.disabled = false;
  deleteCloseButton.textContent = "Cancel";
  deleteModal.show();
}

async function confirmDelete() {
  if (!pendingDeleteSlug) return;
  confirmDeleteButton.disabled = true;
  confirmDeleteButton.innerHTML =
    '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Deleting...';
  deleteCloseButton.disabled = true;
  try {
    const response = await fetch(`/api/instances/${encodeURIComponent(pendingDeleteSlug)}`, {
      method: "DELETE",
      headers: {
        "X-Delete-Remote": deleteRemoteCheckbox.checked ? "true" : "false"
      }
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Delete failed.");
    instances = result.catalog.items || [];
    render();
    deleteConfirmation.classList.add("d-none");
    deleteResult.className = "alert alert-success mb-0";
    deleteResult.textContent = result.remoteDeleted
      ? "The local instance and Viewer documentation were deleted successfully."
      : "The local instance and scan results were deleted successfully.";
    confirmDeleteButton.classList.add("d-none");
    deleteCloseButton.textContent = "Close";
  } catch (error) {
    deleteConfirmation.classList.add("d-none");
    deleteResult.className = "alert alert-danger mb-0";
    deleteResult.textContent = error.message;
    confirmDeleteButton.classList.add("d-none");
    deleteCloseButton.textContent = "Close";
  } finally {
    confirmDeleteButton.disabled = false;
    deleteCloseButton.disabled = false;
  }
}

async function syncWithViewer(manual = false) {
  syncButton.disabled = true;
  const original = syncButton.innerHTML;
  syncButton.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Syncing';
  syncStatus.textContent = "Synchronizing status with Viewer...";
  try {
    const response = await fetch("/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Sync failed.");
    instances = result.catalog.items || [];
    render();
    syncStatus.textContent = `Last synced ${formatDate(result.lastSyncedAt)} - ${result.remoteCount} documents found.`;
  } catch (error) {
    syncStatus.textContent = `Sync failed: ${error.message}`;
    if (error.message.toLowerCase().includes("register again")) {
      syncStatus.innerHTML = `${escapeHtml(error.message)} <a href="/settings.html">Open Settings</a>`;
    }
    if (manual) window.alert(`${error.message}\n\nThe last local status has been preserved.`);
  } finally {
    syncButton.disabled = false;
    syncButton.innerHTML = original;
  }
}

rows.addEventListener("click", (event) => {
  const publishButton = event.target.closest("[data-publish]");
  if (publishButton) {
    openPublishModal(publishButton.dataset.publish);
    return;
  }
  const deleteButton = event.target.closest("[data-delete]");
  if (deleteButton) openDeleteModal(deleteButton.dataset.delete);
});

confirmPublishButton.addEventListener("click", confirmPublish);
confirmDeleteButton.addEventListener("click", confirmDelete);
publishModalElement.addEventListener("hidden.bs.modal", () => {
  pendingPublishSlug = "";
  publishInstanceName.textContent = "";
  publishInstanceSlug.textContent = "";
  publishConfirmation.classList.remove("d-none");
  publishResult.className = "alert d-none mb-0";
  publishResult.textContent = "";
  confirmPublishButton.classList.remove("d-none");
  confirmPublishButton.disabled = false;
  publishCloseButton.disabled = false;
  publishCloseButton.textContent = "Cancel";
});

deleteModalElement.addEventListener("hidden.bs.modal", () => {
  pendingDeleteSlug = "";
  deleteInstanceName.textContent = "";
  deleteInstanceSlug.textContent = "";
  deleteViewerOption.classList.add("d-none");
  deleteRemoteCheckbox.checked = false;
  deleteConfirmation.classList.remove("d-none");
  deleteResult.className = "alert d-none mb-0";
  deleteResult.textContent = "";
  confirmDeleteButton.classList.remove("d-none");
  confirmDeleteButton.disabled = false;
  confirmDeleteButton.innerHTML = '<i class="bi bi-trash3 me-1" aria-hidden="true"></i>Delete';
  deleteCloseButton.disabled = false;
  deleteCloseButton.textContent = "Cancel";
});

searchInput.addEventListener("input", render);
syncButton.addEventListener("click", () => syncWithViewer(true));

loadInstances()
  .then(() => syncWithViewer(false))
  .catch((error) => {
    meta.textContent = error.message;
    emptyState.style.display = "block";
  });

window.setInterval(() => {
  if (document.visibilityState === "visible") syncWithViewer(false);
}, 10 * 60 * 1000);

