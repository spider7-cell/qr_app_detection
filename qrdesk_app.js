const state = {
  selectedFiles: [],
  scans: [],
  activeScanId: null,
  pollHandle: null,
  scanAfterPick: false,
  previewUrl: null,
  activeImageId: null,
  activeScan: null,
  modalOpen: false,
  modalZoom: 1,
  modalPanX: 0,
  modalPanY: 0,
  modalDragging: false,
  dragStartX: 0,
  dragStartY: 0,
  compareSplit: 50,
  compareDragging: false,
  cameraDevices: [],
  cameraDeviceId: "",
  cameraStream: null,
  cameraActive: false,
  capturedFromCamera: false,
  sourceMode: "upload",
  historyQuery: "",
  historyStatus: "all",
  adminModalOpen: false,
  aboutModalOpen: false,
  adminSettings: {
    default_profile: "fast",
    default_workers: 2,
    export_folder: "",
  },
};

function $(id) {
  return document.getElementById(id);
}

function setStatus(message, tone = "idle") {
  const el = $("status-banner");
  const compact = String(message || "").replace(/\s+/g, " ").trim();
  const shortMessage = compact.length > 64 ? `${compact.slice(0, 61)}...` : compact;
  el.textContent = shortMessage || "Ready.";
  el.className = `status-banner ${tone}`;
}

function syncToolbarProfile(value) {
  const toolbarProfile = $("toolbar-profile");
  if (!toolbarProfile) {
    return;
  }
  toolbarProfile.textContent = value || "fast";
}

function syncToolbarContext(scanLabel = "No scan", imageLabel = "None") {
  const toolbarScan = $("toolbar-scan");
  const toolbarImage = $("toolbar-image");
  if (toolbarScan) {
    toolbarScan.textContent = scanLabel || "No scan";
  }
  if (toolbarImage) {
    toolbarImage.textContent = imageLabel || "None";
  }
}

function syncToolbarUser(username = "admin") {
  const toolbarUser = $("toolbar-user");
  if (!toolbarUser) {
    return;
  }
  toolbarUser.textContent = username || "admin";
}

function setPasswordMessage(message = "", tone = "") {
  const box = $("password-message");
  if (!box) return;
  box.textContent = message;
  box.className = `password-message ${tone}`.trim();
}

function setSettingsMessage(message = "", tone = "") {
  const box = $("settings-message");
  if (!box) return;
  box.textContent = message;
  box.className = `password-message ${tone}`.trim();
}
function openAdminModal() {
  const modal = $("admin-modal");
  const current = $("current-password");
  if (!modal) return;
  setPasswordMessage("");
  loadAdminSettings();
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  state.adminModalOpen = true;
  setTimeout(() => current?.focus(), 0);
}

function closeAdminModal() {
  const modal = $("admin-modal");
  const form = $("password-form");
  if (!modal) return;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  form?.reset();
  setPasswordMessage("");
  setSettingsMessage("");
  document.body.classList.remove("modal-open");
  state.adminModalOpen = false;
}

async function submitPasswordChange(event) {
  event.preventDefault();
  const current = $("current-password");
  const next = $("new-password");
  const confirm = $("confirm-password");
  const save = $("password-save-btn");
  if (!current || !next || !confirm || !save) return;

  if (next.value.length < 8) {
    setPasswordMessage("Use at least 8 characters for the new password.", "error");
    return;
  }
  if (next.value !== confirm.value) {
    setPasswordMessage("The confirmation does not match the new password.", "error");
    return;
  }

  save.disabled = true;
  save.textContent = "Saving...";
  setPasswordMessage("Updating password...", "running");
  try {
    await fetchJSON("/api/admin/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: current.value,
        new_password: next.value,
      }),
    });
    current.value = "";
    next.value = "";
    confirm.value = "";
    setPasswordMessage("Password updated. Use it at the next login.", "success");
    setStatus("Admin password updated.", "success");
  } catch (error) {
    setPasswordMessage(error.message || "Unable to update password.", "error");
    setStatus(error.message || "Unable to update password.", "error");
  } finally {
    save.disabled = false;
    save.textContent = "Save password";
  }
}
function applyAdminSettings(settings = {}) {
  const profile = "fast";
  const workers = Number(settings.default_workers || 2);
  const exportFolder = settings.export_folder || "";
  state.adminSettings = {
    default_profile: profile,
    default_workers: Number.isFinite(workers) ? workers : 2,
    export_folder: exportFolder,
  };

  const mainProfile = $("profile");
  const mainWorkers = $("workers");
  const settingsProfile = $("settings-default-profile");
  const settingsWorkers = $("settings-default-workers");
  const settingsFolder = $("settings-export-folder");

  if (mainProfile) mainProfile.value = state.adminSettings.default_profile;
  if (mainWorkers) mainWorkers.value = String(state.adminSettings.default_workers);
  if (settingsProfile) settingsProfile.value = state.adminSettings.default_profile;
  if (settingsWorkers) settingsWorkers.value = String(state.adminSettings.default_workers);
  if (settingsFolder) settingsFolder.value = state.adminSettings.export_folder;
  syncToolbarProfile(state.adminSettings.default_profile);
}

async function loadAdminSettings() {
  setSettingsMessage("Loading settings...", "running");
  try {
    const data = await fetchJSON("/api/admin/settings");
    applyAdminSettings(data.settings || {});
    setSettingsMessage("Settings loaded.", "success");
  } catch (error) {
    setSettingsMessage(error.message || "Unable to load settings.", "error");
  }
}

async function submitAdminSettings(event) {
  event.preventDefault();
  const save = $("settings-save-btn");
  const profile = "fast";
  const workers = Number($("settings-default-workers")?.value || 2);
  const exportFolder = $("settings-export-folder")?.value || "";
  if (save) {
    save.disabled = true;
    save.textContent = "Saving...";
  }
  setSettingsMessage("Saving settings...", "running");
  try {
    const data = await fetchJSON("/api/admin/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        default_profile: profile,
        default_workers: workers,
        export_folder: exportFolder,
      }),
    });
    applyAdminSettings(data.settings || {});
    setSettingsMessage("Settings saved.", "success");
    setStatus("Admin settings updated.", "success");
  } catch (error) {
    setSettingsMessage(error.message || "Unable to save settings.", "error");
    setStatus(error.message || "Unable to save settings.", "error");
  } finally {
    if (save) {
      save.disabled = false;
      save.textContent = "Save settings";
    }
  }
}

function resetAdminSettings() {
  applyAdminSettings({ default_profile: "fast", default_workers: 2, export_folder: "" });
  setSettingsMessage("Defaults staged. Click Save settings to apply.", "running");
}
function openAboutModal() {
  const modal = $("about-modal");
  const version = $("about-version");
  if (!modal) return;
  if (version) version.textContent = `v${window.__APP_BOOT__?.appVersion || "1.0"}`;
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  state.aboutModalOpen = true;
}

function closeAboutModal() {
  const modal = $("about-modal");
  if (!modal) return;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  state.aboutModalOpen = false;
}

async function openExportFolder() {
  const button = $("open-export-folder-btn");
  if (button) button.disabled = true;
  setStatus("Opening export folder...", "running");
  try {
    const data = await fetchJSON("/api/admin/open-export-folder", { method: "POST" });
    setStatus(`Export folder opened: ${data.path || "ready"}`, "success");
  } catch (error) {
    setStatus(error.message || "Unable to open export folder.", "error");
  } finally {
    if (button) button.disabled = false;
  }
}
function formatProfileLabel(value) {
  if (!value) return "fast";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function updateModalZoom() {
  const img = $("image-modal-img");
  const readout = $("image-modal-zoom-reset");
  const stage = $("image-modal-stage");
  if (!img || !readout || !stage) return;
  img.style.transform = `translate(${state.modalPanX}px, ${state.modalPanY}px) scale(${state.modalZoom})`;
  readout.textContent = `${Math.round(state.modalZoom * 100)}%`;
  stage.classList.toggle("is-draggable", state.modalZoom > 1.01);
}

function setModalZoom(nextZoom) {
  const prev = state.modalZoom;
  state.modalZoom = Math.max(0.5, Math.min(4, nextZoom));
  if (state.modalZoom <= 1.01) {
    state.modalPanX = 0;
    state.modalPanY = 0;
  } else if (prev <= 1.01) {
    state.modalPanX = 0;
    state.modalPanY = 0;
  }
  updateModalZoom();
}

function openImageModal(src, title) {
  const modal = $("image-modal");
  const img = $("image-modal-img");
  const label = $("image-modal-title");
  if (!modal || !img || !label || !src) return;
  state.modalZoom = 1;
  state.modalPanX = 0;
  state.modalPanY = 0;
  img.src = src;
  img.alt = title || "Expanded scan preview";
  label.textContent = title || "Preview";
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  state.modalOpen = true;
  updateModalZoom();
}

function closeImageModal() {
  const modal = $("image-modal");
  const img = $("image-modal-img");
  if (!modal || !img) return;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  img.removeAttribute("src");
  img.style.transform = "translate(0px, 0px) scale(1)";
  document.body.classList.remove("modal-open");
  state.modalOpen = false;
  state.modalZoom = 1;
  state.modalPanX = 0;
  state.modalPanY = 0;
  state.modalDragging = false;
}

function startModalDrag(event) {
  const stage = $("image-modal-stage");
  if (!stage || state.modalZoom <= 1.01) return;
  state.modalDragging = true;
  state.dragStartX = event.clientX - state.modalPanX;
  state.dragStartY = event.clientY - state.modalPanY;
  stage.classList.add("dragging");
}

function moveModalDrag(event) {
  if (!state.modalDragging) return;
  state.modalPanX = event.clientX - state.dragStartX;
  state.modalPanY = event.clientY - state.dragStartY;
  updateModalZoom();
}

function stopModalDrag() {
  const stage = $("image-modal-stage");
  state.modalDragging = false;
  if (stage) stage.classList.remove("dragging");
}

function bindCompareResize(container) {
  const handle = container.querySelector(".compare-resizer");
  const board = container.querySelector(".compare-board");
  if (!handle || !board) return;

  const applySplit = () => {
    board.style.setProperty("--compare-split", `${state.compareSplit}%`);
  };
  applySplit();

  handle.addEventListener("pointerdown", (event) => {
    state.compareDragging = true;
    handle.setPointerCapture?.(event.pointerId);
    document.body.classList.add("compare-dragging");
  });

  const move = (event) => {
    if (!state.compareDragging) return;
    const rect = board.getBoundingClientRect();
    if (!rect.width) return;
    const ratio = ((event.clientX - rect.left) / rect.width) * 100;
    state.compareSplit = Math.max(28, Math.min(72, ratio));
    applySplit();
  };

  const stop = () => {
    state.compareDragging = false;
    document.body.classList.remove("compare-dragging");
  };

  handle.addEventListener("pointermove", move);
  handle.addEventListener("pointerup", stop);
  handle.addEventListener("pointercancel", stop);
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop);
}

function updateSelectionHint() {
  const hint = $("selection-hint");
  const openBtn = $("open-files-btn");
  const scanBtn = $("run-upload-btn");
  if (!hint || !openBtn || !scanBtn) {
    return;
  }

  if (!state.selectedFiles.length) {
    hint.textContent = "No image selected yet.";
    hint.className = "selection-hint";
    openBtn.textContent = "Upload";
    scanBtn.textContent = "Scan";
    return;
  }

  const count = state.selectedFiles.length;
  hint.textContent = `${count} image${count > 1 ? "s" : ""} selected. Ready to scan.`;
  hint.className = "selection-hint ready";
  openBtn.textContent = count > 1 ? "Change Images" : "Change Image";
  scanBtn.textContent = count > 1 ? `Scan ${count} Images` : "Scan 1 Image";
}

function revokePreviewUrl() {
  if (state.previewUrl) {
    URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = null;
  }
}

function clearSelectedImages(fileInput = null) {
  state.selectedFiles = [];
  state.capturedFromCamera = false;
  state.scanAfterPick = false;
  state.sourceMode = "upload";
  if (fileInput) {
    fileInput.value = "";
  }
  showPreScanWorkspace();
  renderSelectedFiles();
  applySourceUi();
  setStatus("Selection cancelled. Ready for upload or camera.", "idle");
}

function applySourceUi() {
  const mode = state.sourceMode || "upload";
  const stage = $("drop-zone");
  const panel = document.querySelector(".camera-center-panel");
  const uploadBtn = $("source-upload-btn");
  const cameraBtn = $("source-camera-btn");
  const hasImage = state.selectedFiles.length > 0;

  uploadBtn?.classList.toggle("active", mode === "upload");
  cameraBtn?.classList.toggle("active", mode === "camera");
  stage?.classList.toggle("source-upload-mode", mode === "upload");
  stage?.classList.toggle("source-camera-mode", mode === "camera");
  if (panel) {
    panel.classList.toggle("hidden", mode !== "camera" || hasImage);
  }
}

function setInputSource(mode, options = {}) {
  state.sourceMode = mode === "camera" ? "camera" : "upload";
  if (state.sourceMode === "upload" && options.stopCamera !== false) {
    stopCameraStream();
  }
  applySourceUi();
  updatePreScanPreview();
  if (!options.silent) {
    setStatus(state.sourceMode === "camera" ? "Camera source selected." : "Upload source selected.", "idle");
  }
  if (state.sourceMode === "camera") {
    refreshCameraDevices();
  }
}

function showPreScanWorkspace() {
  const loading = $("detail-loading");
  const empty = $("detail-empty");
  const content = $("detail-content");
  const header = $("detail-header");
  const historyPage = $("history-page");
  if (loading) loading.classList.add("hidden");
  if (empty) empty.classList.remove("hidden");
  if (content) content.classList.add("hidden");
  if (header) header.classList.add("hidden");
  if (historyPage) historyPage.classList.add("hidden");
  state.activeScanId = null;
  state.activeScan = null;
  state.activeImageId = null;
}

function updatePreScanPreview() {
  const preview = $("pre-scan-preview");
  const clearPreview = $("clear-preview-btn");
  const camera = $("camera-preview");
  const previewCopy = $("pre-scan-copy");
  const previewFrame = document.querySelector(".empty-preview");
  const previewStage = document.querySelector(".empty-stage");
  if (!preview || !previewCopy || !previewFrame) {
    return;
  }
  applySourceUi();

  if (!state.selectedFiles.length) {
    revokePreviewUrl();
    preview.removeAttribute("src");
    preview.classList.add("hidden");
    clearPreview?.classList.add("hidden");
    previewFrame.classList.remove("has-image");
    if (previewStage) {
      previewStage.classList.remove("has-image");
    }
    if (camera) {
      camera.classList.toggle("hidden", !state.cameraActive);
      previewFrame.classList.toggle("camera-live", !!state.cameraActive);
      if (previewStage) previewStage.classList.toggle("camera-mode", !!state.cameraActive);
    }
    previewCopy.textContent = state.cameraActive
      ? "External camera live. Align the board, then capture."
      : (state.sourceMode === "camera" ? "Start the external camera, capture a board image, then scan." : "Drop an image here or click Upload to start.");
    if (!state.activeScan) {
      syncToolbarContext(state.cameraActive ? "External camera" : "No scan", state.cameraActive ? "Live preview" : "None");
    }
    return;
  }

  revokePreviewUrl();
  state.previewUrl = URL.createObjectURL(state.selectedFiles[0]);
  preview.src = state.previewUrl;
  preview.classList.remove("hidden");
  clearPreview?.classList.remove("hidden");
  if (camera) camera.classList.add("hidden");
  previewFrame.classList.add("has-image");
  previewFrame.classList.remove("camera-live");
  if (previewStage) {
    previewStage.classList.add("has-image");
    previewStage.classList.remove("camera-mode");
  }
  applySourceUi();

  const count = state.selectedFiles.length;
  const firstName = state.selectedFiles[0].name;
  previewCopy.textContent =
    count === 1
      ? `Ready to scan: ${firstName}`
      : `Ready to scan ${count} images. Showing preview: ${firstName}`;
  if (!state.activeScan) {
    syncToolbarContext(state.capturedFromCamera ? "Camera capture" : "Ready to scan", count === 1 ? firstName : `${count} images`);
  }
}

function hasCameraSupport() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}

function setCameraStatus(message, tone = "") {
  const el = $("camera-status");
  if (!el) return;
  el.textContent = message;
  el.className = `camera-status ${tone}`.trim();
}

function stopCameraStream() {
  if (state.cameraStream) {
    state.cameraStream.getTracks().forEach((track) => track.stop());
  }
  state.cameraStream = null;
  state.cameraActive = false;
  const preview = $("camera-preview");
  if (preview) {
    preview.pause?.();
    preview.srcObject = null;
    preview.classList.add("hidden");
  }
  updatePreScanPreview();
}

async function refreshCameraDevices() {
  const select = $("camera-device");
  if (!select || !navigator.mediaDevices?.enumerateDevices) return;
  const devices = await navigator.mediaDevices.enumerateDevices();
  state.cameraDevices = devices.filter((device) => device.kind === "videoinput");
  const currentValue = state.cameraDeviceId || select.value || "";
  const options = ['<option value="">Connected camera</option>'];
  state.cameraDevices.forEach((device, index) => {
    const label = device.label || `Camera ${index + 1}`;
    options.push(`<option value="${device.deviceId}">${label}</option>`);
  });
  select.innerHTML = options.join("");
  if (currentValue && state.cameraDevices.some((device) => device.deviceId === currentValue)) {
    select.value = currentValue;
    state.cameraDeviceId = currentValue;
  } else {
    state.cameraDeviceId = select.value || "";
  }
}

async function startCameraPreview() {
  if (!hasCameraSupport()) {
    setCameraStatus("Camera access is not available in this browser.", "error");
    setStatus("Camera access is not available.", "error");
    return;
  }
  try {
    stopCameraStream();
    const constraints = state.cameraDeviceId
      ? { video: { deviceId: { exact: state.cameraDeviceId } }, audio: false }
      : { video: true, audio: false };
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    state.cameraStream = stream;
    state.cameraActive = true;
    const preview = $("camera-preview");
    if (preview) {
      preview.srcObject = stream;
      await preview.play().catch(() => null);
      preview.classList.remove("hidden");
    }
    await refreshCameraDevices();
    setCameraStatus("External camera live. Align the board, then capture.", "live");
    setStatus("External camera started.", "success");
    updatePreScanPreview();
  } catch (error) {
    setCameraStatus(error.message || "Unable to start the external camera.", "error");
    setStatus(error.message || "Unable to start the external camera.", "error");
  }
}

function cameraCaptureFilename() {
  const now = new Date();
  const stamp = now.toISOString().replace(/[-:T]/g, "").slice(0, 15);
  return `camera_capture_${stamp}.jpg`;
}

async function captureCameraFrame() {
  const preview = $("camera-preview");
  const canvas = $("camera-canvas");
  if (!state.cameraActive || !preview || !canvas) {
    setStatus("Start the external camera before capturing.", "error");
    setCameraStatus("Start the external camera before capturing.", "warn");
    return false;
  }
  const width = preview.videoWidth || 1280;
  const height = preview.videoHeight || 720;
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(preview, 0, 0, width, height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.95));
  if (!blob) {
    setStatus("Capture failed.", "error");
    return false;
  }
  const file = new File([blob], cameraCaptureFilename(), { type: "image/jpeg" });
  state.selectedFiles = [file];
  state.capturedFromCamera = true;
  renderSelectedFiles();
  setCameraStatus(`Captured ${file.name}. Ready to scan.`, "live");
  setStatus("Camera photo captured.", "success");
  return true;
}

async function scanCapturedFrame() {
  const ready = state.capturedFromCamera && state.selectedFiles.length;
  if (!ready) {
    const captured = await captureCameraFrame();
    if (!captured) return;
  }
  await runUploadedImages();
}


function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatSeconds(value) {
  if (value == null) return "-";
  return `${Number(value).toFixed(1)}s`;
}

function renderSelectedFiles() {
  const hasSelection = state.selectedFiles.length > 0;
  if (hasSelection) {
    showPreScanWorkspace();
  }

  const container = $("selected-files");
  if (!container) {
    if (hasSelection) {
      const count = state.selectedFiles.length;
      setStatus(`${count} image${count > 1 ? "s" : ""} selected. Ready to scan.`, "idle");
    }
    updateSelectionHint();
    updatePreScanPreview();
    renderScanList();
    return;
  }

  if (!hasSelection) {
    container.textContent = "No files selected yet.";
    container.className = "file-list empty";
    updateSelectionHint();
    updatePreScanPreview();
    return;
  }
  container.className = "file-list";
  container.innerHTML = state.selectedFiles
    .map((file) => `<span class="file-chip">${file.name}</span>`)
    .join("");
  updateSelectionHint();
  updatePreScanPreview();
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Authentication required");
  }
  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }
  return data;
}

async function refreshScans() {
  const previousActive = state.scans.find((scan) => scan.id === state.activeScanId);
  const previousSignature = scanStateSignature(previousActive);
  const data = await fetchJSON("/api/scans");
  state.scans = data.items || [];
  renderScanList();
  if (state.activeScanId) {
    const nextActive = state.scans.find((scan) => scan.id === state.activeScanId);
    const nextSignature = scanStateSignature(nextActive);
    if (nextSignature && nextSignature !== previousSignature) {
      await loadScanDetail(state.activeScanId, false, true);
    }
  }
}

function scanStateSignature(scan) {
  if (!scan) return "";
  return [
    scan.id,
    scan.status,
    scan.processed_images,
    scan.total_images,
    scan.total_qr,
    scan.localized_total_qr,
    scan.total_expected,
    scan.error || ""
  ].join("|");
}

function scanBadge(status) {
  return `<span class="badge ${status}">${status}</span>`;
}

function filteredHistoryScans() {
  const query = (state.historyQuery || "").trim().toLowerCase();
  const status = state.historyStatus || "all";
  return state.scans.filter((scan) => {
    const statusOk = status === "all" || scan.status === status;
    const text = [scan.source_label, scan.profile, scan.status, scan.id].filter(Boolean).join(" ").toLowerCase();
    const queryOk = !query || text.includes(query);
    return statusOk && queryOk;
  });
}

function renderHistoryPage() {
  const page = $("history-page");
  const list = $("history-page-list");
  const search = $("history-search");
  const statusFilter = $("history-status-filter");
  if (!page || !list) return;
  if (search && search.value !== state.historyQuery) search.value = state.historyQuery || "";
  if (statusFilter && statusFilter.value !== state.historyStatus) statusFilter.value = state.historyStatus || "all";

  if (!state.scans.length) {
    list.innerHTML = `<div class="history-empty-card">No saved scans yet. Upload an image or run the reference set to create history.</div>`;
    return;
  }

  const scans = filteredHistoryScans();
  if (!scans.length) {
    list.innerHTML = `<div class="history-empty-card">No scans match this filter.</div>`;
    return;
  }

  list.innerHTML = scans.map((scan) => {
    const progress = scan.total_images
      ? Math.max(6, Math.min(100, Math.round((scan.processed_images / scan.total_images) * 100)))
      : 6;
    const elapsed = scanElapsedText(scan);
    return `
      <article class="history-run-card" data-history-scan-id="${scan.id}">
        <div class="history-run-main">
          <div>
            <span class="section-kicker">${scan.profile || "scan"}</span>
            <h3>${scan.source_label}</h3>
          </div>
          ${scanBadge(scan.status)}
        </div>
        <div class="history-run-stats">
          <span><strong>${scan.total_qr || 0}/${scan.total_expected || 0}</strong> detected</span>
          <span>${scan.processed_images || 0}/${scan.total_images || 0} images</span>
          <span>${formatDate(scan.created_at)}</span>
          ${elapsed !== "-" ? `<span>${elapsed}</span>` : ""}
        </div>
        <div class="scan-progress"><span style="width: ${progress}%"></span></div>
        <div class="history-run-actions">
          <button type="button" class="mini-action" data-history-open="${scan.id}">Open result</button>
          ${(scan.status === "running" || scan.status === "queued") ? `<button type="button" class="scan-stop" data-scan-stop="${scan.id}">Stop</button>` : ""}
          ${(scan.status === "completed" || scan.status === "failed" || scan.status === "cancelled") ? `<button type="button" class="scan-delete" data-scan-delete="${scan.id}">Delete</button>` : ""}
        </div>
      </article>
    `;
  }).join("");

  list.querySelectorAll("[data-history-open], .history-run-card").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      const scanId = el.dataset.historyOpen || el.dataset.historyScanId;
      if (scanId) loadScanDetail(scanId, true);
    });
  });
  list.querySelectorAll("[data-history-open]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      loadScanDetail(button.dataset.historyOpen, true);
    });
  });
  list.querySelectorAll(".scan-stop").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        setStatus("Stopping scan...", "running");
        await cancelScan(button.dataset.scanStop);
        setStatus("Stop requested.", "success");
        renderHistoryPage();
      } catch (error) {
        setStatus(error.message, "error");
      }
    });
  });
  list.querySelectorAll(".scan-delete").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        setStatus("Deleting scan...", "running");
        await deleteScan(button.dataset.scanDelete);
        setStatus("Scan deleted.", "success");
        renderHistoryPage();
      } catch (error) {
        setStatus(error.message, "error");
      }
    });
  });
}

function openHistoryPage() {
  const loading = $("detail-loading");
  const empty = $("detail-empty");
  const content = $("detail-content");
  const header = $("detail-header");
  const page = $("history-page");
  if (loading) loading.classList.add("hidden");
  if (empty) empty.classList.add("hidden");
  if (content) content.classList.add("hidden");
  if (header) header.classList.add("hidden");
  if (page) page.classList.remove("hidden");
  renderHistoryPage();
  syncToolbarContext("Scan History", "All saved runs");
}

function renderScanList() {
  const container = $("scan-list");
  const meta = $("scan-list-meta");
  if (meta) {
    const running = state.scans.filter((scan) => scan.status === "running" || scan.status === "queued").length;
    const completed = state.scans.filter((scan) => scan.status === "completed").length;
    const failed = state.scans.filter((scan) => scan.status === "failed").length;
    meta.innerHTML = state.scans.length
      ? `
        <span class="meta-chip">${state.scans.length} saved</span>
        <span class="meta-chip running">${running} running</span>
        <span class="meta-chip completed">${completed} completed</span>
        ${failed ? `<span class="meta-chip failed">${failed} failed</span>` : ""}
        ${state.scans.filter((scan) => scan.status === "cancelled").length ? `<span class="meta-chip cancelled">${state.scans.filter((scan) => scan.status === "cancelled").length} cancelled</span>` : ""}
      `
      : "No saved scans yet.";
  }
  if (!state.scans.length) {
    container.innerHTML = `<div class="detail-empty">No scans yet. Start one from the control panel.</div>`;
    renderHistoryPage();
    return;
  }

  const sidebarScans = state.scans.slice(0, 5);
  container.innerHTML = sidebarScans
    .map((scan) => {
      const activeClass = scan.id === state.activeScanId ? "active" : "";
      const progress = scan.total_images
        ? Math.max(6, Math.min(100, Math.round((scan.processed_images / scan.total_images) * 100)))
        : 6;
      return `
        <article class="scan-item ${activeClass}" data-scan-id="${scan.id}">
          <div class="scan-item-head">
            <div class="scan-item-title-wrap">
              <h3>${scan.source_label}</h3>
              <p>${scanCountText(scan)}</p>
            </div>
            <div class="scan-item-actions">
              ${scanBadge(scan.status)}
              ${scan.status === "running" || scan.status === "queued"
                ? `<button type="button" class="scan-stop" data-scan-stop="${scan.id}" title="Stop scan">Stop</button>`
                : (scan.status === "completed" || scan.status === "failed" || scan.status === "cancelled"
                  ? `<button type="button" class="scan-delete" data-scan-delete="${scan.id}" title="Delete scan">Delete</button>`
                  : "")}
            </div>
          </div>
          <div class="scan-item-meta compact">
            <small>${formatDate(scan.created_at)}${scanElapsedText(scan) !== "-" ? ` | ${scanElapsedText(scan)}` : ""}</small>
          </div>
          <div class="scan-progress"><span style="width: ${progress}%"></span></div>
        </article>
      `;
    })
    .join("") + (state.scans.length > sidebarScans.length ? `<button type="button" class="history-more-card" id="history-more-card">View all ${state.scans.length} saved runs</button>` : "");
  renderHistoryPage();

  $("history-more-card")?.addEventListener("click", openHistoryPage);

  container.querySelectorAll(".scan-item").forEach((item) => {
    item.addEventListener("click", () => loadScanDetail(item.dataset.scanId, true));
  });
  container.querySelectorAll(".scan-stop").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const scanId = button.dataset.scanStop;
      if (!scanId) return;
      try {
        setStatus("Stopping scan...", "running");
        await cancelScan(scanId);
        setStatus("Stop requested.", "success");
      } catch (error) {
        setStatus(error.message, "error");
      }
    });
  });
  container.querySelectorAll(".scan-delete").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const scanId = button.dataset.scanDelete;
      if (!scanId) return;
      try {
        setStatus("Deleting scan...", "running");
        await deleteScan(scanId);
        setStatus("Scan deleted.", "success");
      } catch (error) {
        setStatus(error.message, "error");
      }
    });
  });
}

function setDetailLoading(title = "Loading scan...", copy = "Preparing the current workspace.") {
  const loading = $("detail-loading");
  const empty = $("detail-empty");
  const content = $("detail-content");
  const historyPage = $("history-page");
  const titleEl = $("detail-loading-title");
  const copyEl = $("detail-loading-copy");
  if (titleEl) titleEl.textContent = title;
  if (copyEl) copyEl.textContent = copy;
  if (loading) loading.classList.remove("hidden");
  if (empty) empty.classList.add("hidden");
  if (content) content.classList.add("hidden");
  if (historyPage) historyPage.classList.add("hidden");
}

function hideDetailLoading() {
  const loading = $("detail-loading");
  if (loading) loading.classList.add("hidden");
}

function renderExportSection(title, exports) {
  return `
    <div class="export-block">
      <strong>${title}</strong>
      ${renderExportLinks(exports)}
    </div>
  `;
}

function renderExportLinks(exports) {
  const links = [];
  if (exports?.qr_csv_url) {
    links.push(`<button type="button" class="export-link" data-export-url="${exports.qr_csv_url}" data-export-name="${exports.qr_csv_name || "detected_qr_codes.csv"}"><span>QR Table</span><small>${exports.qr_csv_name || "detected_qr_codes.csv"}</small></button>`);
  }
  if (exports?.excel_table_url) {
    links.push(`<button type="button" class="export-link" data-export-url="${exports.excel_table_url}" data-export-name="${exports.excel_table_name || "scan_tables.xls"}"><span>Excel</span><small>${exports.excel_table_name || "scan_tables.xls"}</small></button>`);
  }

  return `
    <div class="export-panel compact-export-panel">
      <span>Exports</span>
      <div class="export-links">
        ${links.length ? links.join("") : `<span class="export-note">Files appear when the scan finishes.</span>`}
      </div>
    </div>
  `;
}

function renderDetailHeader(scan) {
  const header = $("detail-header");
  const label = $("detail-scan-label");
  const meta = $("detail-scan-meta");
  const badge = $("detail-scan-badge");
  if (!header || !label || !meta || !badge) {
    return;
  }

  const activeImage = state.activeScan?.images?.find((image) => image.id === state.activeImageId);
  const overview = state.activeImageId === "__overview__" && (state.activeScan?.images?.length || 0) > 1;
  header.classList.remove("hidden");
  label.textContent = overview ? (scan.source_label || "Run overview") : (activeImage?.filename || scan.source_label || "Scan");
  meta.textContent = overview
    ? `${scanCountText(scan)} | ${scan.total_images} images | ${scanElapsedText(scan, state.activeScan?.images || [])}`
    : activeImage
      ? `${imageCountText(activeImage)} | ${formatSeconds(activeImage.elapsed)}`
      : (scanCountText(scan) || "");
  badge.textContent = scan.status;
  badge.className = `detail-scan-badge ${scan.status}`;
  syncToolbarProfile(scan.profile);
  syncToolbarContext(scan.source_label || "Scan", overview ? "All images" : (activeImage?.filename || "None"));
}

function renderSummary(scan, exports) {
  const activeImage = state.activeScan?.images?.find((image) => image.id === state.activeImageId);
  if (activeImage && state.activeImageId !== "__overview__") {
    $("scan-summary").innerHTML = `
      <div class="summary-grid compact simple-right-rail dual-export-summary">
        ${renderExportSection("This Image", activeImage.exports || {})}
        ${renderExportSection("Full Scan", exports || {})}
      </div>
    `;
    return;
  }
  $("scan-summary").innerHTML = `<div class="summary-grid compact simple-right-rail">${renderExportSection("Full Scan", exports || {})}</div>`;
}

function renderProcessingState() {
  const preview = state.previewUrl
    ? `<img src="${state.previewUrl}" alt="Processing image preview">`
    : `<div class="detail-empty">Preparing preview...</div>`;
  const label = state.selectedFiles.length
    ? state.selectedFiles[0].name
    : (state.activeScan?.source_label || "Current run");
  const profile = formatProfileLabel(state.activeScan?.profile || $("profile")?.value || "fast");
  return `
    <article class="processing-state">
      <div class="processing-head compact">
        <div>
          <h3>${label}</h3>
          <p>${profile} scan in progress.</p>
        </div>
        <div class="processing-badge"><span class="processing-spinner"></span>Scanning</div>
      </div>
      <div class="processing-stage">
        ${preview}
        <div class="processing-overlay">
          <span class="processing-spinner"></span>
          <strong>Scanning image</strong>
          <p>Your image is being analyzed now.</p>
        </div>
      </div>
    </article>
  `;
}

function imageDecodedCount(image) {
  return Number(image?.decoded_count ?? image?.qr_count ?? 0);
}

function imageLocalizedCount(image) {
  return Number(image?.localized_count ?? imageDecodedCount(image));
}

function imageCountText(image) {
  const decoded = imageDecodedCount(image);
  const expected = Number(image?.expected_qr || 0);
  return `${decoded}/${expected} detected`;
}

function scanCountText(scan) {
  const decoded = Number(scan?.total_qr || 0);
  const expected = Number(scan?.total_expected || scan?.total_images || 0);
  return `${decoded}/${expected} detected`;
}

function scanElapsedText(scan, images = null) {
  const explicit = Number(scan?.total_elapsed || 0);
  if (explicit > 0) {
    return formatSeconds(explicit);
  }
  const list = Array.isArray(images) ? images : (Array.isArray(scan?.images) ? scan.images : []);
  if (!list.length) {
    return '-';
  }
  const total = list.reduce((sum, image) => sum + Number(image?.elapsed || 0), 0);
  return total > 0 ? formatSeconds(total) : '-';
}

function decodedRows(image) {
  if (!Array.isArray(image?.decoded_rows)) {
    return [];
  }
  return image.decoded_rows;
}

function renderQuickQrData(image) {
  const rows = decodedRows(image);
  const quickCards = rows.length
    ? rows.map((row, index) => `
        <details class="qr-code-card">
          <summary>
            <span class="qr-code-index">QR ${index + 1}</span>
            <span class="qr-code-state">Decoded</span>
          </summary>
          <div class="qr-code-details">
            <label>IMEI</label>
            <strong>${row.imei || 'No IMEI'}</strong>
            <label>Serial number</label>
            <strong>${row.serial || 'No serial'}</strong>
          </div>
        </details>
      `).join("")
    : `<div class="qr-empty-note">No decoded QR data saved for this image yet.</div>`;

  return `
    <section class="qr-data-panel simple-qr-panel compact-qr-data">
      <div class="qr-data-head simple-qr-head">
        <strong>Decoded QR codes</strong>
        <span class="simple-count-chip">${imageCountText(image)}</span>
      </div>
      <div class="qr-quick-grid qr-card-grid">${quickCards}</div>
      <p class="qr-data-hint">Click a QR card to show its IMEI and serial number.</p>
    </section>
  `;
}

function renderStoredCodesSummary(image) {
  const count = imageDecodedCount(image);
  const localized = imageLocalizedCount(image);
  const expected = Number(image.expected_qr || 0);
  return `
    <div class="stored-codes ${count ? "" : "empty"}">
      <strong>${image.filename}</strong>
      <p>${localized > count ? `${count}/${expected} decoded | ${localized} localized.` : `${count}/${expected} decoded.`} Full values are saved in the export files.</p>
    </div>
  `;
}

function renderImageStrip(images) {
  const container = $("image-strip");
  if (!container) return;
  if (!images.length || images.length === 1) {
    container.innerHTML = "";
    return;
  }

  const tabs = [`
      <button type="button" class="image-tab overview-tab ${state.activeImageId === "__overview__" ? "active" : ""}" data-image-id="__overview__">
        <span>All Results</span>
        <small>${images.length} images</small>
      </button>
    `];

  tabs.push(...images.map((image) => `
      <button type="button" class="image-tab ${image.id === state.activeImageId ? "active" : ""}" data-image-id="${image.id}">
        <span>${image.filename}</span>
        <small>${imageCountText(image)}</small>
      </button>
    `));

  container.innerHTML = tabs.join("");
  container.querySelectorAll(".image-tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeImageId = button.dataset.imageId;
      renderDetailHeader(state.activeScan);
      renderSummary(state.activeScan, state.activeScan.exports || {});
      renderImages(images);
    });
  });
}

function renderOverview(scan, images) {
  return `
    <section class="overview-board">
      <div class="overview-head">
        <div>
          <strong>All Results</strong>
          <p>${scanCountText(scan)} across ${images.length} images${scanElapsedText(scan, images) !== "-" ? ` | ${scanElapsedText(scan, images)}` : ""}</p>
        </div>
      </div>
      <div class="overview-grid">
        ${images.map((image) => `
          <article class="overview-card" data-open-image="${image.id}">
            <div class="overview-card-head">
              <strong>${image.filename}</strong>
              <span>${imageCountText(image)}</span>
            </div>
            <div class="overview-card-meta">${formatSeconds(image.elapsed)}${image.error ? ` | ${image.error}` : ""}</div>
            <div class="overview-actions">
              <button type="button" class="mini-action" data-open-image="${image.id}">Open result</button>
              ${image.annotated_url ? `<button type="button" class="mini-action" data-view-src="${image.annotated_url}" data-view-title="${image.filename} | Annotated">View image</button>` : ""}
            </div>
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

function renderActiveImage(image, index, total) {
  const original = image.original_url
    ? `
      <div class="viewer-card">
        <div class="viewer-topbar">
          <div class="viewer-label">Original</div>
          <button type="button" class="viewer-eye" data-view-src="${image.original_url}" data-view-title="${image.filename} | Original" aria-label="View original image">View</button>
        </div>
        <div class="image-frame"><img src="${image.original_url}" alt="${image.filename} original"></div>
      </div>
    `
    : `<div class="detail-empty">Original image unavailable.</div>`;
  const annotated = image.annotated_url
    ? `
      <div class="viewer-card">
        <div class="viewer-topbar">
          <div class="viewer-label">Annotated</div>
          <button type="button" class="viewer-eye" data-view-src="${image.annotated_url}" data-view-title="${image.filename} | Annotated" aria-label="View image image">View</button>
        </div>
        <div class="image-frame"><img src="${image.annotated_url}" alt="${image.filename} annotated"></div>
      </div>
    `
    : `<div class="detail-empty">Annotation unavailable.</div>`;

  return `
    <article class="active-result compact simple-result">
      <div class="simple-result-head">
        <div>
          <h3>${image.filename}</h3>
          <p>${imageCountText(image)} | ${formatSeconds(image.elapsed)}</p>
        </div>
        ${scanBadge(image.error ? "failed" : "completed")}
      </div>
      <div class="compare-board simple-static-compare">
        ${original}
        ${annotated}
      </div>
      ${renderQuickQrData(image)}
    </article>
  `;
}

function renderImages(images) {
  const container = $("image-results");
  if (!container) {
    return;
  }

  if (!images.length) {
    $("image-strip").innerHTML = "";
    if (state.activeScan && (state.activeScan.status === "running" || state.activeScan.status === "queued")) {
      container.innerHTML = renderProcessingState();
    } else {
      container.innerHTML = `<div class="detail-empty">No image results are available for this scan yet.</div>`;
    }
    hideDetailLoading();
    return;
  }

  if (images.length > 1 && !state.activeImageId) {
    state.activeImageId = "__overview__";
  }
  const active = images.find((image) => image.id === state.activeImageId) || images[0];
  renderImageStrip(images);

  if (images.length > 1 && state.activeImageId === "__overview__") {
    container.innerHTML = renderOverview(state.activeScan, images);
    container.querySelectorAll("[data-open-image]").forEach((button) => {
      button.addEventListener("click", () => {
        state.activeImageId = button.dataset.openImage;
        renderDetailHeader(state.activeScan);
        renderSummary(state.activeScan, state.activeScan.exports || {});
        renderImages(images);
      });
    });
    container.querySelectorAll("[data-view-src]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        openImageModal(button.dataset.viewSrc, button.dataset.viewTitle);
      });
    });
    hideDetailLoading();
    syncToolbarContext(state.activeScan?.source_label || "Scan", "All images");
    return;
  }

  state.activeImageId = active.id;
  container.innerHTML = renderActiveImage(active, 0, images.length);
  container.querySelectorAll(".viewer-eye").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openImageModal(button.dataset.viewSrc, button.dataset.viewTitle);
    });
  });
  hideDetailLoading();
  syncToolbarContext(state.activeScan?.source_label || "Scan", active.filename || "None");
}

async function loadScanDetail(scanId, fromUserClick = false, skipListRender = false) {
  const switchingScan = state.activeScanId !== scanId;
  if (fromUserClick || !state.activeScanId) {
    setDetailLoading("Loading scan...", "Opening the selected run and preparing its current image.");
  }
  const data = await fetchJSON(`/api/scans/${scanId}`);
  state.activeScanId = scanId;
  state.activeScan = data.scan;
  state.activeScan.images = data.images;
  state.activeScan.exports = data.exports;
  if (switchingScan) {
    state.activeImageId = data.images.length > 1 ? "__overview__" : (data.images[0]?.id || null);
  } else if (!state.activeImageId && data.images.length) {
    state.activeImageId = data.images.length > 1 ? "__overview__" : data.images[0].id;
  }
  renderScanList();
  hideDetailLoading();
  $("detail-empty").classList.add("hidden");
  $("history-page")?.classList.add("hidden");
  $("detail-content").classList.remove("hidden");
  renderDetailHeader(data.scan);
  renderSummary(data.scan, data.exports);
  renderImages(data.images);

  if (data.scan.status === "running" || data.scan.status === "queued") {
    if (!state.pollHandle || fromUserClick) {
      startPolling(scanId);
    }
  } else if (state.pollHandle) {
    clearInterval(state.pollHandle);
    state.pollHandle = null;
  }
}

function startPolling(scanId) {
  if (state.pollHandle) {
    clearInterval(state.pollHandle);
  }
  state.pollHandle = setInterval(async () => {
    try {
      await refreshScans();
      const scan = state.scans.find((item) => item.id === scanId);
      if (!scan || (scan.status !== "running" && scan.status !== "queued")) {
        clearInterval(state.pollHandle);
        state.pollHandle = null;
      }
    } catch (error) {
      clearInterval(state.pollHandle);
      state.pollHandle = null;
      setStatus(error.message, "error");
    }
  }, 4500);
}

function currentOptions() {
  return {
    profile: "fast",
    deep_timeout: Number($("deep-timeout").value || 0),
    workers: Number($("workers").value || 1),
  };
}

async function createUploadSession() {
  return fetchJSON("/api/upload-sessions", { method: "POST" });
}

async function uploadSelectedFiles(sessionId) {
  for (let index = 0; index < state.selectedFiles.length; index += 1) {
    const file = state.selectedFiles[index];
    setStatus(`Uploading ${index + 1}/${state.selectedFiles.length}...`, "running");
    const response = await fetch(
      `/api/upload-sessions/${sessionId}/files/${encodeURIComponent(file.name)}`,
      {
        method: "PUT",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
        },
        body: file,
      }
    );
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `Failed to upload ${file.name}`);
    }
  }
}

async function startScan(payload) {
  return fetchJSON("/api/scans", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function cancelScan(scanId) {
  const attempts = [
    { url: `/api/scans/${scanId}/cancel`, method: "POST" },
    { url: `/api/scans/${scanId}/stop`, method: "POST" },
    { url: `/api/scans/${scanId}/cancel`, method: "GET" },
  ];

  let lastError = "Failed to stop scan";
  for (const attempt of attempts) {
    const response = await fetch(attempt.url, { method: attempt.method });
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      await refreshScans();
      if (state.activeScanId === scanId) {
        await loadScanDetail(scanId, false);
      }
      return;
    }
    lastError = data.detail || lastError;
    if (response.status !== 404) {
      throw new Error(lastError);
    }
  }

  throw new Error(lastError);
}

async function deleteScan(scanId) {
  const response = await fetch(`/api/scans/${scanId}/delete`, { method: "POST" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Failed to delete scan");
  }
  if (state.activeScanId === scanId) {
    state.activeScanId = null;
    state.activeScan = null;
    state.activeImageId = null;
    const detailHeader = $("detail-header");
    if (detailHeader) detailHeader.classList.add("hidden");
    $("detail-content")?.classList.add("hidden");
    $("history-page")?.classList.add("hidden");
    $("detail-empty")?.classList.remove("hidden");
    syncToolbarContext("No scan", "None");
  }
  await refreshScans();
  renderScanList();
}

async function logoutUser() {
  await fetch('/api/auth/logout', { method: 'POST' }).catch(() => null);
  window.location.href = '/login';
}

function renderShutdownScreen() {
  document.body.innerHTML = `
    <main class="shutdown-screen">
      <section class="shutdown-card">
        <span class="shutdown-mark">QR</span>
        <h1>QR Desk stopped</h1>
        <p>The local scanner server has been closed. You can close this browser tab now.</p>
        <small>To start again, double-click the QR Desk shortcut on the Desktop.</small>
      </section>
    </main>
  `;
}

async function quitApplication() {
  const confirmed = window.confirm('Quit QR Desk definitively and stop the local server?');
  if (!confirmed) return;

  setStatus('Quitting QR Desk...', 'running');
  if (state.pollHandle) {
    clearInterval(state.pollHandle);
    state.pollHandle = null;
  }
  stopCameraStream();

  try {
    await fetchJSON('/api/admin/shutdown', { method: 'POST' });
  } catch (error) {
    // The server can close before the browser finishes reading the response.
  } finally {
    renderShutdownScreen();
  }
}
async function downloadExportFile(url, filename) {
  const response = await fetch(url);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Failed to download ${filename}`);
  }

  const blob = await response.blob();
  const blobUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(blobUrl);

  const lower = (filename || "").toLowerCase();
  const label = lower.endsWith('.xls') ? 'Excel table' : lower.endsWith('.json') ? 'JSON file' : 'CSV file';
  setStatus(`${label} downloaded.`, 'success');
}

async function runUploadedImages() {
  if (!state.selectedFiles.length) {
    state.scanAfterPick = true;
    setStatus("Choose at least one image first.", "error");
    $("file-input").click();
    return;
  }

  state.scanAfterPick = false;
  const opts = currentOptions();
  syncToolbarProfile(opts.profile);
  try {
    setStatus("Creating upload session...", "running");
    const session = await createUploadSession();
    await uploadSelectedFiles(session.session_id);
    const scan = await startScan({
      source_type: "upload",
      session_id: session.session_id,
      profile: opts.profile,
      deep_timeout: opts.deep_timeout || null,
      workers: opts.workers,
      label: state.selectedFiles.length === 1 ? state.selectedFiles[0].name : `Uploaded batch (${state.selectedFiles.length} images)`,
    });
    setDetailLoading("Starting scan...", "Uploading is complete. The detector is preparing the new job.");
    setStatus("Scan started.", "success");
    await refreshScans();
    await loadScanDetail(scan.scan_id, true);
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function runSampleSet() {
  const opts = currentOptions();
  syncToolbarProfile(opts.profile);
  try {
    setStatus("Queueing reference set...", "running");
    const scan = await startScan({
      source_type: "sample",
      sample_files: window.__APP_BOOT__.sampleImages,
      profile: opts.profile,
      deep_timeout: opts.deep_timeout || null,
      workers: opts.workers,
      label: "Reference Image Set",
    });
    setDetailLoading("Starting reference set...", "Opening the saved reference images in the current workspace.");
    setStatus("Reference set running.", "success");
    await refreshScans();
    await loadScanDetail(scan.scan_id, true);
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function wireEvents() {
  const fileInput = $("file-input");
  const dropZone = $("drop-zone");
  const modal = $("image-modal");
  const closeModal = $("image-modal-close");
  fileInput.addEventListener("change", (event) => {
    state.selectedFiles = Array.from(event.target.files || []);
    state.capturedFromCamera = false;
    state.sourceMode = "upload";
    renderSelectedFiles();
    if (!state.selectedFiles.length) {
      state.scanAfterPick = false;
      return;
    }
    if (state.scanAfterPick) {
      runUploadedImages();
    }
  });

  if (dropZone) {
    ["dragenter", "dragover"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.add("dragover");
      });
    });
    ["dragleave", "dragend", "drop"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.remove("dragover");
      });
    });
    dropZone.addEventListener("drop", (event) => {
      const files = Array.from(event.dataTransfer?.files || []).filter((file) => /\.(jpg|jpeg|png|bmp|tiff|webp)$/i.test(file.name));
      if (!files.length) {
        setStatus("Drop image files only.", "error");
        return;
      }
      state.selectedFiles = files;
      state.capturedFromCamera = false;
      state.sourceMode = "upload";
      renderSelectedFiles();
      setStatus(`${files.length} image${files.length > 1 ? "s" : ""} ready.`, "success");
    });
  }

  if (dropZone) {
    dropZone.addEventListener("click", (event) => {
      if (event.target.closest(".image-tab") || event.target.closest(".export-link") || event.target.closest(".source-option") || event.target.closest(".camera-center-panel") || event.target.closest(".preview-clear")) {
        return;
      }
      if (!state.selectedFiles.length && state.sourceMode !== "camera") {
        fileInput.click();
      }
    });
  }

  if (closeModal) {
    closeModal.addEventListener("click", closeImageModal);
  }
  if (modal) {
    modal.addEventListener("click", (event) => {
      if (event.target.dataset.closeModal === "true" || event.target === modal) {
        closeImageModal();
      }
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (state.modalOpen) {
      closeImageModal();
    }
    if (state.adminModalOpen) {
      closeAdminModal();
    }
    if (state.aboutModalOpen) {
      closeAboutModal();
    }
  });
  const zoomIn = $("image-modal-zoom-in");
  const zoomOut = $("image-modal-zoom-out");
  const zoomReset = $("image-modal-zoom-reset");
  const modalStage = $("image-modal-stage");

  if (zoomIn) zoomIn.addEventListener("click", () => setModalZoom(state.modalZoom + 0.2));
  if (zoomOut) zoomOut.addEventListener("click", () => setModalZoom(state.modalZoom - 0.2));
  if (zoomReset) zoomReset.addEventListener("click", () => setModalZoom(1));
  if (modalStage) {
    modalStage.addEventListener("wheel", (event) => {
      if (!state.modalOpen) return;
      event.preventDefault();
      setModalZoom(state.modalZoom + (event.deltaY < 0 ? 0.12 : -0.12));
    }, { passive: false });
    modalStage.addEventListener("pointerdown", (event) => {
      if (event.target.id === "image-modal-img") {
        startModalDrag(event);
      }
    });
  }
  window.addEventListener("pointermove", moveModalDrag);
  window.addEventListener("pointerup", stopModalDrag);
  window.addEventListener("pointercancel", stopModalDrag);

  syncToolbarProfile("fast");

  const openFilesBtn = $("open-files-btn");
  if (openFilesBtn) {
    openFilesBtn.addEventListener("click", () => {
      state.capturedFromCamera = false;
      state.sourceMode = "upload";
      applySourceUi();
      fileInput.value = "";
      fileInput.click();
    });
  }

  $("source-upload-btn")?.addEventListener("click", () => setInputSource("upload"));
  $("source-camera-btn")?.addEventListener("click", () => setInputSource("camera"));

  const cameraDevice = $("camera-device");
  if (cameraDevice) {
    cameraDevice.addEventListener("change", () => {
      state.cameraDeviceId = cameraDevice.value || "";
      if (state.cameraActive) {
        startCameraPreview();
      }
    });
  }
  $("start-camera-btn")?.addEventListener("click", startCameraPreview);
  $("stop-camera-btn")?.addEventListener("click", () => {
    stopCameraStream();
    setCameraStatus("External camera stopped.");
    setStatus("External camera stopped.", "idle");
  });
  $("capture-camera-btn")?.addEventListener("click", captureCameraFrame);
  $("scan-camera-btn")?.addEventListener("click", scanCapturedFrame);
  navigator.mediaDevices?.addEventListener?.("devicechange", refreshCameraDevices);

  $("open-history-btn")?.addEventListener("click", openHistoryPage);
  $("scan-list-meta")?.addEventListener("click", openHistoryPage);
  $("history-back-btn")?.addEventListener("click", showPreScanWorkspace);
  $("history-search")?.addEventListener("input", (event) => {
    state.historyQuery = event.target.value || "";
    renderHistoryPage();
  });
  $("history-status-filter")?.addEventListener("change", (event) => {
    state.historyStatus = event.target.value || "all";
    renderHistoryPage();
  });
  $("history-clear-filters")?.addEventListener("click", () => {
    state.historyQuery = "";
    state.historyStatus = "all";
    renderHistoryPage();
  });

  const securityBtn = $("security-btn");
  const adminModal = $("admin-modal");
  if (securityBtn) securityBtn.addEventListener("click", openAdminModal);
  $("admin-modal-close")?.addEventListener("click", closeAdminModal);
  $("password-cancel-btn")?.addEventListener("click", closeAdminModal);
  $("password-form")?.addEventListener("submit", submitPasswordChange);
  $("settings-form")?.addEventListener("submit", submitAdminSettings);
  $("settings-reset-btn")?.addEventListener("click", resetAdminSettings);
  if (adminModal) {
    adminModal.addEventListener("click", (event) => {
      if (event.target.dataset.closeAdmin === "true" || event.target === adminModal) {
        closeAdminModal();
      }
    });
  }
  const aboutBtn = $("about-btn");
  const aboutModal = $("about-modal");
  if (aboutBtn) aboutBtn.addEventListener("click", openAboutModal);
  $("about-modal-close")?.addEventListener("click", closeAboutModal);
  if (aboutModal) {
    aboutModal.addEventListener("click", (event) => {
      if (event.target.dataset.closeAbout === "true" || event.target === aboutModal) {
        closeAboutModal();
      }
    });
  }
  $("open-export-folder-btn")?.addEventListener("click", openExportFolder);
  const logoutBtn = $("logout-btn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", async () => {
      logoutBtn.disabled = true;
      try {
        await logoutUser();
      } finally {
        logoutBtn.disabled = false;
      }
    });
  }

  const quitAppBtn = $("quit-app-btn");
  if (quitAppBtn) {
    quitAppBtn.addEventListener("click", async () => {
      quitAppBtn.disabled = true;
      try {
        await quitApplication();
      } finally {
        quitAppBtn.disabled = false;
      }
    });
  }
  const clearFilesBtn = $("clear-files-btn");
  if (clearFilesBtn) {
    clearFilesBtn.addEventListener("click", () => clearSelectedImages(fileInput));
  }
  const clearPreviewBtn = $("clear-preview-btn");
  if (clearPreviewBtn) {
    clearPreviewBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      clearSelectedImages(fileInput);
    });
  }

  $("run-upload-btn").addEventListener("click", runUploadedImages);
  $("run-sample-btn").addEventListener("click", runSampleSet);
  document.addEventListener("click", async (event) => {
    const button = event.target.closest(".export-link");
    if (!button) {
      return;
    }
    const url = button.dataset.exportUrl;
    const name = button.dataset.exportName;
    if (!url || !name) {
      return;
    }
    try {
      setStatus(`Downloading ${name}...`, "running");
      await downloadExportFile(url, name);
      setStatus(`${name} downloaded.`, "success");
    } catch (error) {
      setStatus(error.message, "error");
    }
  });
}

async function boot() {
  applyAdminSettings(window.__APP_BOOT__?.settings || {});
  wireEvents();
  renderSelectedFiles();
  updateSelectionHint();
  syncToolbarProfile($("profile")?.value || "fast");
  syncToolbarUser(window.__APP_BOOT__?.authUser?.username || "admin");
  const detailHeader = $("detail-header");
  if (detailHeader) {
    detailHeader.classList.add("hidden");
  }
  hideDetailLoading();
  try {
    syncToolbarContext("No scan", "None");
    await refreshCameraDevices();
    await refreshScans();
    const appPath = window.location.pathname.toLowerCase().replace(/\/+$/, "");
    if (["/history", "/saved-runs", "/runs", "/scans"].includes(appPath)) {
      openHistoryPage();
    } else if (state.scans.length) {
      await loadScanDetail(state.scans[0].id, false);
    }
    setStatus("Ready to scan.", "idle");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

boot();



