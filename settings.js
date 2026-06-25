const form = document.querySelector("#integration-form");
const viewerUrl = document.querySelector("#viewer-url");
const scannerName = document.querySelector("#scanner-name");
const installationId = document.querySelector("#installation-id");
const statusBox = document.querySelector("#integration-status");
const testButton = document.querySelector("#test-button");
const saveButton = document.querySelector("#save-button");

function updateSaveButton(status) {
  const registered = ["pending", "approved", "revoked"].includes(status);
  saveButton.innerHTML = registered
    ? '<i class="bi bi-arrow-repeat me-1"></i>Update Data'
    : '<i class="bi bi-link-45deg me-1"></i>Register Scanner';
}

function setStatus(message, type = "secondary") {
  statusBox.textContent = message;
  statusBox.className = `alert alert-${type}`;
}

function statusMessage(status) {
  if (status === "approved") return ["Scanner approved and ready to publish.", "success"];
  if (status === "revoked") return ["Scanner access has been revoked by the Viewer administrator.", "danger"];
  if (status === "pending") return ["Registration submitted. Waiting for Viewer administrator approval.", "warning"];
  return ["Enter the Viewer URL and register this scanner.", "secondary"];
}

async function loadSettings() {
  const response = await fetch("/api/settings", { cache: "no-store" });
  const settings = await response.json();
  if (!response.ok) throw new Error(settings.error || "Settings could not be loaded.");
  viewerUrl.value = settings.viewerUrl || "http://127.0.0.1:8090";
  scannerName.value = settings.scannerName || "";
  installationId.value = settings.installationId || "Created during registration";
  updateSaveButton(settings.registrationStatus);
  const [message, type] = statusMessage(settings.registrationStatus);
  setStatus(message, type);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  saveButton.disabled = true;
  setStatus("Registering scanner with Viewer...");
  try {
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        viewerUrl: viewerUrl.value.trim(),
        scannerName: scannerName.value.trim()
      })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Registration failed.");
    installationId.value = result.installationId;
    updateSaveButton(result.registrationStatus);
    const [message, type] = statusMessage(result.registrationStatus);
    setStatus(message, type);
  } catch (error) {
    setStatus(error.message, "danger");
  } finally {
    saveButton.disabled = false;
  }
});

testButton.addEventListener("click", async () => {
  testButton.disabled = true;
  setStatus("Checking scanner status...");
  try {
    const response = await fetch("/api/settings/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Connection failed.");
    const [message, type] = statusMessage(result.status);
    updateSaveButton(result.status);
    setStatus(`${message} Connected to ${result.service}.`, type);
  } catch (error) {
    setStatus(error.message, "danger");
  } finally {
    testButton.disabled = false;
  }
});

loadSettings().catch((error) => setStatus(error.message, "danger"));

