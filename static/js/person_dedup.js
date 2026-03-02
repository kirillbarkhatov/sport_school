document.addEventListener("DOMContentLoaded", () => {
  const config = window.personDedupConfig || {};
  const startForm = document.getElementById("dedup-start-form");
  const clustersBody = document.getElementById("clusters-body");
  const statusFilter = document.getElementById("cluster-status-filter");
  const summaryEl = document.getElementById("job-summary");
  const kpiEl = document.getElementById("job-kpis");
  const modalEl = document.getElementById("clusterModal");
  const modalContent = document.getElementById("cluster-modal-content");
  const clusterModal =
    typeof bootstrap !== "undefined" && modalEl
      ? bootstrap.Modal.getOrCreateInstance(modalEl)
      : null;

  let currentJobId = Number.isInteger(config.latestJobId)
    ? config.latestJobId
    : null;
  let statusTimer = null;

  const showToast = (message, variant = "success") => {
    if (window.showToast) {
      window.showToast(message, variant);
      return;
    }
    console.log(message);
  };

  const getCsrfToken = () => {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : "";
  };

  const buildUrl = (template, value) => template.replace("/0/", `/${value}/`);

  const renderJobSummary = (job) => {
    if (!job || !summaryEl || !kpiEl) {
      return;
    }
    summaryEl.textContent = `ID ${job.id}, статус: ${job.status}, режим: ${job.mode}, dry-run: ${
      job.dry_run ? "да" : "нет"
    }`;
    kpiEl.innerHTML = `
      <span>Кластеры: ${job.duplicate_clusters_found}</span>
      <span>Автообъединено: ${job.auto_merged_clusters}</span>
      <span>Конфликты: ${job.conflicts_count}</span>
      <span>Обработано персон: ${job.processed_persons}/${job.total_persons}</span>
    `;
  };

  const renderClusters = (clusters) => {
    if (!clustersBody) {
      return;
    }
    if (!clusters.length) {
      clustersBody.innerHTML = `
        <tr>
          <td colspan="6" class="text-center text-muted py-4">Кластеры не найдены.</td>
        </tr>
      `;
      return;
    }
    const rows = clusters
      .map((cluster, index) => {
        const members = cluster.items
          .map((item) => `${item.full_name} (#${item.person_id})`)
          .join("<br>");
        const canResolve = cluster.status === "open";
        return `
          <tr data-cluster-id="${cluster.id}">
            <td class="ps-3">${index + 1}</td>
            <td><span class="badge text-bg-${
              cluster.status === "open" ? "warning" : "secondary"
            }">${cluster.status}</span></td>
            <td>${cluster.max_pair_score}</td>
            <td class="small">${members}</td>
            <td class="small text-muted">${cluster.reason_summary || "—"}</td>
            <td class="text-end pe-3">
              <button class="btn btn-sm btn-outline-primary" data-action="open-cluster" data-cluster-id="${
                cluster.id
              }">Открыть</button>
              ${
                canResolve
                  ? `<button class="btn btn-sm btn-outline-secondary" data-action="skip-cluster" data-cluster-id="${cluster.id}">Пропустить</button>`
                  : ""
              }
            </td>
          </tr>
        `;
      })
      .join("");
    clustersBody.innerHTML = rows;
  };

  const loadClusters = async () => {
    if (!currentJobId) {
      renderClusters([]);
      return;
    }
    try {
      const status = statusFilter?.value || "";
      const url = `${buildUrl(
        config.urlClustersTemplate,
        currentJobId
      )}?status=${encodeURIComponent(status)}`;
      const response = await fetch(url);
      const payload = await response.json();
      if (!response.ok || !payload.success) {
        return;
      }
      renderClusters(payload.clusters || []);
    } catch (error) {
      console.error("Failed to load clusters", error);
    }
  };

  const loadJobStatus = async () => {
    if (!currentJobId) {
      return;
    }
    try {
      const response = await fetch(buildUrl(config.urlJobStatusTemplate, currentJobId));
      const payload = await response.json();
      if (!response.ok || !payload.success) {
        return;
      }
      renderJobSummary(payload.job);
      if (payload.job.status === "queued" || payload.job.status === "running") {
        if (!statusTimer) {
          statusTimer = setInterval(loadJobStatus, 3000);
        }
      } else if (statusTimer) {
        clearInterval(statusTimer);
        statusTimer = null;
      }
      await loadClusters();
    } catch (error) {
      console.error("Failed to load job status", error);
    }
  };

  const openCluster = async (clusterId) => {
    if (!clusterModal || !modalContent) {
      return;
    }
    modalContent.textContent = "Загрузка...";
    clusterModal.show();
    try {
      const response = await fetch(buildUrl(config.urlClusterDetailTemplate, clusterId));
      const payload = await response.json();
      if (!response.ok || !payload.success) {
        modalContent.textContent = "Не удалось загрузить кластер.";
        return;
      }
      const cluster = payload.cluster;
      const personOptions = cluster.items
        .map((item) => `<option value="${item.person_id}">${item.full_name} (#${item.person_id})</option>`)
        .join("");
      const personCards = cluster.items
        .map(
          (item) => `
          <div class="card mb-2">
            <div class="card-body py-2">
              <div class="d-flex justify-content-between align-items-start">
                <div>
                  <div class="fw-semibold">${item.full_name} (#${item.person_id})</div>
                  <div class="small text-muted">ДР: ${item.date_of_birth || "—"}, пол: ${item.gender || "—"}</div>
                  <div class="small text-muted">Тел: ${item.phone || "—"}, Email: ${item.email || "—"}, TG: ${item.telegram || "—"}</div>
                  <div class="small text-muted">Клуб: ${item.club || "—"}</div>
                </div>
                <div class="form-check">
                  <input class="form-check-input" type="radio" name="master_person_id" value="${item.person_id}" ${
                    item.is_suggested_master ? "checked" : ""
                  }>
                  <label class="form-check-label">Master</label>
                </div>
              </div>
            </div>
          </div>
        `
        )
        .join("");

      const fields = [
        ["surname", "Фамилия"],
        ["name", "Имя"],
        ["middlename", "Отчество"],
        ["date_of_birth", "Дата рождения"],
        ["phone", "Телефон"],
        ["email", "Email"],
        ["telegram", "Telegram"],
        ["club", "Клуб"],
        ["gender", "Пол"],
        ["comment", "Комментарий"],
      ];

      const resolutionRows = fields
        .map(
          ([fieldKey, fieldLabel]) => `
          <div class="col-md-6">
            <label class="form-label small">${fieldLabel}</label>
            <select class="form-select form-select-sm" name="field_${fieldKey}_person_id">
              <option value="">Авто</option>
              ${personOptions}
            </select>
          </div>
        `
        )
        .join("");

      modalContent.innerHTML = `
        <div class="mb-2">
          <span class="badge text-bg-secondary">Статус: ${cluster.status}</span>
          <span class="badge text-bg-info">Score: ${cluster.max_pair_score}</span>
        </div>
        <div class="small text-muted mb-2">Причины: ${cluster.reason_summary || "—"}</div>
        <form id="cluster-merge-form" data-cluster-id="${cluster.id}">
          <div class="mb-3">${personCards}</div>
          <div class="border rounded p-2 mb-3">
            <div class="fw-semibold mb-2">Разрешение полей</div>
            <div class="row g-2">${resolutionRows}</div>
          </div>
          <div class="mb-3">
            <label class="form-label">Комментарий</label>
            <textarea class="form-control form-control-sm" rows="2" name="note"></textarea>
          </div>
          <div class="d-flex gap-2 justify-content-end">
            <button type="button" class="btn btn-outline-secondary btn-sm" data-action="modal-skip" data-cluster-id="${cluster.id}">Пропустить</button>
            <button type="submit" class="btn btn-primary btn-sm">Применить merge</button>
          </div>
        </form>
      `;
    } catch (error) {
      console.error("Failed to load cluster", error);
      modalContent.textContent = "Ошибка загрузки.";
    }
  };

  const postForm = async (url, formData) => {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
      },
      body: formData,
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || "request_failed");
    }
    return payload;
  };

  startForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(startForm);
    const button = startForm.querySelector('[data-role="start-btn"]');
    if (button) {
      button.disabled = true;
    }
    try {
      const payload = await postForm(config.urlStart, formData);
      currentJobId = payload.job.id;
      renderJobSummary(payload.job);
      showToast("Задача дедупликации запущена.");
      await loadJobStatus();
    } catch (error) {
      showToast("Не удалось запустить задачу дедупликации.", "danger");
      console.error(error);
    } finally {
      if (button) {
        button.disabled = false;
      }
    }
  });

  statusFilter?.addEventListener("change", () => {
    loadClusters();
  });

  clustersBody?.addEventListener("click", async (event) => {
    const target = event.target.closest("[data-action]");
    if (!target) {
      return;
    }
    const clusterId = Number(target.dataset.clusterId);
    if (!clusterId) {
      return;
    }
    if (target.dataset.action === "open-cluster") {
      openCluster(clusterId);
      return;
    }
    if (target.dataset.action === "skip-cluster") {
      const formData = new FormData();
      const csrfToken = getCsrfToken();
      if (csrfToken) {
        formData.append("csrfmiddlewaretoken", csrfToken);
      }
      try {
        await postForm(buildUrl(config.urlClusterSkipTemplate, clusterId), formData);
        showToast("Кластер помечен как пропущенный.");
        await loadClusters();
      } catch (error) {
        showToast("Не удалось обновить статус кластера.", "danger");
      }
    }
  });

  modalEl?.addEventListener("submit", async (event) => {
    const form = event.target.closest("#cluster-merge-form");
    if (!form) {
      return;
    }
    event.preventDefault();
    const clusterId = Number(form.dataset.clusterId);
    const formData = new FormData(form);
    const csrfToken = getCsrfToken();
    if (csrfToken && !formData.get("csrfmiddlewaretoken")) {
      formData.append("csrfmiddlewaretoken", csrfToken);
    }
    try {
      await postForm(buildUrl(config.urlClusterMergeTemplate, clusterId), formData);
      showToast("Кластер успешно объединён.");
      clusterModal?.hide();
      await loadJobStatus();
    } catch (error) {
      showToast("Не удалось применить merge.", "danger");
    }
  });

  modalEl?.addEventListener("click", async (event) => {
    const target = event.target.closest('[data-action="modal-skip"]');
    if (!target) {
      return;
    }
    const clusterId = Number(target.dataset.clusterId);
    const formData = new FormData();
    const csrfToken = getCsrfToken();
    if (csrfToken) {
      formData.append("csrfmiddlewaretoken", csrfToken);
    }
    try {
      await postForm(buildUrl(config.urlClusterSkipTemplate, clusterId), formData);
      clusterModal?.hide();
      showToast("Кластер пропущен.");
      await loadClusters();
    } catch (error) {
      showToast("Не удалось пропустить кластер.", "danger");
    }
  });

  if (currentJobId) {
    loadJobStatus();
  }
});
