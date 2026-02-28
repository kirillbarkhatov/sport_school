(() => {
  const getCookie = (name) => {
    const cookieString = document.cookie;
    if (!cookieString) {
      return "";
    }
    const parts = cookieString.split(";").map((item) => item.trim());
    for (const item of parts) {
      if (item.startsWith(`${name}=`)) {
        return decodeURIComponent(item.slice(name.length + 1));
      }
    }
    return "";
  };

  const csrfToken = getCookie("csrftoken");
  let timerInterval = null;
  let pollingInterval = null;

  const stopProcessingIndicators = () => {
    if (timerInterval) {
      clearInterval(timerInterval);
      timerInterval = null;
    }
    if (pollingInterval) {
      clearInterval(pollingInterval);
      pollingInterval = null;
    }
  };

  const normalizeClarifyUrl = (template, athleteDocumentId) =>
    template.replace("/0/clarify/", `/${athleteDocumentId}/clarify/`);

  const buildPreviewSrc = (url, kind) => {
    if (!url) {
      return "";
    }
    if (kind === "image") {
      return url;
    }
    // For PDF viewers in iframe, fit content to page width on mobile.
    return `${url}#view=FitH&zoom=page-width`;
  };

  const installPreviewAutoClose = (previewModal, previewModalEl) => {
    if (!previewModalEl) {
      return;
    }
    const closeOnAction = () => {
      previewModal?.hide();
      document.removeEventListener("click", closeOnAction, true);
      document.removeEventListener("touchstart", closeOnAction, true);
      document.removeEventListener("keydown", closeOnAction, true);
    };
    setTimeout(() => {
      document.addEventListener("click", closeOnAction, true);
      document.addEventListener("touchstart", closeOnAction, true);
      document.addEventListener("keydown", closeOnAction, true);
    }, 120);
  };

  const updateCertificateState = (form, payload) => {
    const certWrap = form?.querySelector("[data-certificate-section]");
    if (!certWrap || !payload) {
      return;
    }
    const badgeEl = certWrap.querySelector("[data-cert-badge]");
    const clarifyWrap = certWrap.querySelector("[data-clarify-wrap]");
    const viewBtn = certWrap.querySelector("[data-view-certificate]");
    const active = payload.active_certificate;

    if (badgeEl) {
      const tone = payload.badge_tone || "warning";
      badgeEl.textContent = payload.label || "Данные отсутствуют";
      badgeEl.classList.remove("text-bg-success", "text-bg-warning", "text-bg-danger");
      badgeEl.classList.add(
        tone === "success" ? "text-bg-success" : tone === "danger" ? "text-bg-danger" : "text-bg-warning"
      );
    }

    if (!active) {
      if (viewBtn) {
        viewBtn.classList.add("d-none");
        viewBtn.dataset.previewUrl = "";
        viewBtn.dataset.previewKind = "";
      }
      if (clarifyWrap) {
        clarifyWrap.classList.add("d-none");
      }
      return;
    }

    if (viewBtn) {
      viewBtn.classList.remove("d-none");
      viewBtn.dataset.previewUrl = active.preview_url || "";
      viewBtn.dataset.previewKind = active.preview_kind || "document";
    }

    if (clarifyWrap) {
      clarifyWrap.classList.toggle("d-none", !payload.needs_clarification);
    }
  };

  const fetchModalHtml = async (modalUrl) => {
    const response = await fetch(modalUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } });
    const payload = await response.json();
    if (!response.ok || !payload?.html) {
      throw new Error("failed_to_load_modal_html");
    }
    return payload.html;
  };

  const bindModalInteractions = ({ modalUrl, editModalBody, previewModalEl, previewFrame, previewImage, previewModal }) => {
    const form = editModalBody?.querySelector("#compact-edit-form");
    if (!form) {
      return;
    }

    let finalHandled = false;
    form.dataset.dirty = "0";

    const markDirty = () => {
      form.dataset.dirty = "1";
    };
    form.querySelectorAll("input,select,textarea").forEach((el) => {
      if (!el.hasAttribute("data-certificate-file-input") && !el.hasAttribute("data-certificate-camera-input")) {
        el.addEventListener("input", markDirty);
        el.addEventListener("change", markDirty);
      }
    });

    const renderModalHtml = (html) => {
      editModalBody.innerHTML = html;
      bindModalInteractions({ modalUrl, editModalBody, previewModalEl, previewFrame, previewImage, previewModal });
    };

    const saveForm = async () => {
      const formData = new FormData(form);
      const response = await fetch(modalUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": csrfToken,
          "X-Requested-With": "XMLHttpRequest",
        },
        body: formData,
      });
      const payload = await response.json();
      if (!response.ok || !payload?.html) {
        return false;
      }
      renderModalHtml(payload.html);
      return true;
    };

    const reloadModal = async () => {
      const html = await fetchModalHtml(modalUrl);
      renderModalHtml(html);
    };

    const pollCertificateStatus = (athleteDocumentId, totalSeconds) => {
      const statusUrl = form.dataset.certificateStatusUrl;
      const timerWrap = form.querySelector("[data-ai-timer]");
      const timerValue = form.querySelector("[data-ai-timer-value]");
      const statusText = form.querySelector("[data-ai-status-text]");
      if (!statusUrl || !timerWrap || !timerValue) {
        return;
      }

      stopProcessingIndicators();
      timerWrap.classList.remove("d-none");
      let remaining = Math.max(1, Number(totalSeconds || 60));

      const renderTimer = () => {
        const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
        const ss = String(remaining % 60).padStart(2, "0");
        timerValue.textContent = `${mm}:${ss}`;
      };
      renderTimer();

      timerInterval = setInterval(() => {
        remaining = Math.max(0, remaining - 1);
        renderTimer();
        if (remaining <= 0) {
          clearInterval(timerInterval);
          timerInterval = null;
        }
      }, 1000);

      const fetchStatus = async () => {
        try {
          const response = await fetch(`${statusUrl}?athlete_document_id=${athleteDocumentId}`);
          const payload = await response.json();
          if (!response.ok || !payload?.success) {
            return;
          }
          if (statusText && payload.processing?.message) {
            statusText.textContent = payload.processing.message;
          }
          updateCertificateState(form, payload);

          const state = payload.processing?.state;
          if (state && state !== "processing" && !finalHandled) {
            finalHandled = true;
            stopProcessingIndicators();
            timerWrap.classList.add("d-none");

            if (form.dataset.dirty === "1") {
              await saveForm();
            }
            await reloadModal();
          }
        } catch (error) {
          console.error(error);
        }
      };

      fetchStatus();
      pollingInterval = setInterval(fetchStatus, 1000);
    };

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await saveForm();
      } catch (error) {
        console.error(error);
      }
    });

    const fileInput = form.querySelector("[data-certificate-file-input]");
    const cameraInput = form.querySelector("[data-certificate-camera-input]");
    const selectedNameInput = form.querySelector("[data-selected-file-name]");
    const pickFileBtn = form.querySelector("[data-pick-file]");
    const pickPhotoBtn = form.querySelector("[data-pick-photo]");

    const updateSelectedName = () => {
      const file = fileInput?.files?.[0];
      const photo = cameraInput?.files?.[0];
      const selected = photo || file;
      if (selectedNameInput) {
        selectedNameInput.value = selected ? selected.name : "";
      }
    };

    pickFileBtn?.addEventListener("click", () => fileInput?.click());
    pickPhotoBtn?.addEventListener("click", () => cameraInput?.click());
    fileInput?.addEventListener("change", () => {
      if (cameraInput) {
        cameraInput.value = "";
      }
      updateSelectedName();
    });
    cameraInput?.addEventListener("change", () => {
      if (fileInput) {
        fileInput.value = "";
      }
      updateSelectedName();
    });

    editModalBody.onclick = async (event) => {
      const uploadBtn = event.target.closest("[data-upload-certificate]");
      const viewBtn = event.target.closest("[data-view-certificate]");
      const clarifyBtn = event.target.closest("[data-clarify-submit]");

      if (viewBtn && (previewFrame || previewImage)) {
        event.preventDefault();
        const previewUrl = viewBtn.dataset.previewUrl || "";
        const previewKind = viewBtn.dataset.previewKind || "document";
        if (!previewUrl) {
          return;
        }
        if (previewFrame) {
          previewFrame.classList.add("d-none");
          previewFrame.src = "";
        }
        if (previewImage) {
          previewImage.classList.add("d-none");
          previewImage.src = "";
        }
        if (previewKind === "image" && previewImage) {
          previewImage.src = previewUrl;
          previewImage.classList.remove("d-none");
        } else if (previewFrame) {
          previewFrame.src = buildPreviewSrc(previewUrl, previewKind);
          previewFrame.classList.remove("d-none");
        }
        previewModal?.show();
        installPreviewAutoClose(previewModal, previewModalEl);
        return;
      }

      if (uploadBtn) {
        const uploadUrl = form.dataset.certificateUploadUrl;
        const selected = cameraInput?.files?.[0] || fileInput?.files?.[0];
        if (!uploadUrl || !selected) {
          return;
        }
        const payloadFormData = new FormData();
        payloadFormData.append("certificate", selected);
        try {
          const response = await fetch(uploadUrl, {
            method: "POST",
            headers: {
              "X-CSRFToken": csrfToken,
              "X-Requested-With": "XMLHttpRequest",
            },
            body: payloadFormData,
          });
          const payload = await response.json();
          if (!response.ok || !payload?.success) {
            return;
          }
          if (fileInput) {
            fileInput.value = "";
          }
          if (cameraInput) {
            cameraInput.value = "";
          }
          if (selectedNameInput) {
            selectedNameInput.value = "";
          }
          pollCertificateStatus(payload.athlete_document_id, payload.processing_seconds || 60);
        } catch (error) {
          console.error(error);
        }
        return;
      }

      if (clarifyBtn) {
        const input = form.querySelector("[data-clarify-valid-until]");
        const activeBtn = form.querySelector("[data-view-certificate]");
        const templateUrl = form.dataset.certificateClarifyUrlTemplate;
        if (!input || !templateUrl || !activeBtn?.dataset.previewUrl) {
          return;
        }
        const validUntil = input.value;
        if (!validUntil) {
          return;
        }
        const statusResponse = await fetch(form.dataset.certificateStatusUrl);
        const statusPayload = await statusResponse.json();
        const active = statusPayload?.active_certificate;
        if (!active?.id) {
          return;
        }
        const clarifyUrl = normalizeClarifyUrl(templateUrl, active.id);
        const payloadFormData = new URLSearchParams();
        payloadFormData.set("valid_until", validUntil);
        try {
          const response = await fetch(clarifyUrl, {
            method: "POST",
            headers: {
              "X-CSRFToken": csrfToken,
              "X-Requested-With": "XMLHttpRequest",
              "Content-Type": "application/x-www-form-urlencoded",
            },
            body: payloadFormData.toString(),
          });
          const payload = await response.json();
          if (!response.ok || !payload?.success) {
            return;
          }
          updateCertificateState(form, payload);
        } catch (error) {
          console.error(error);
        }
      }
    };
  };

  const openAthleteModal = async (modalUrl) => {
    const editModalEl = document.getElementById("athleteEditModal");
    const editModalBody = editModalEl?.querySelector("[data-athlete-modal-body]");
    const previewModalEl = document.getElementById("certificatePreviewModal");
    const previewFrame = previewModalEl?.querySelector("[data-certificate-frame]");
    const previewImage = previewModalEl?.querySelector("[data-certificate-image]");
    if (!window.bootstrap || !modalUrl || !editModalBody || !editModalEl) {
      return;
    }
    const editModal = bootstrap.Modal.getOrCreateInstance(editModalEl);
    const previewModal = previewModalEl ? bootstrap.Modal.getOrCreateInstance(previewModalEl) : null;
    editModalBody.innerHTML = '<div class="text-muted">Загрузка...</div>';
    editModal.show();
    stopProcessingIndicators();

    try {
      const html = await fetchModalHtml(modalUrl);
      editModalBody.innerHTML = html;
      bindModalInteractions({ modalUrl, editModalBody, previewModalEl, previewFrame, previewImage, previewModal });
    } catch (error) {
      console.error(error);
      editModalBody.innerHTML = '<div class="text-danger">Ошибка при загрузке формы</div>';
    }
  };

  document.addEventListener("click", (event) => {
    const athleteLink = event.target.closest("[data-athlete-item]");
    if (!athleteLink) {
      return;
    }
    event.preventDefault();
    const modalUrl = athleteLink.dataset.modalUrl;
    if (!modalUrl) {
      window.location.href = athleteLink.href;
      return;
    }
    openAthleteModal(modalUrl);
  });

  document.addEventListener("hidden.bs.modal", (event) => {
    const target = event.target;
    if (target?.id === "athleteEditModal") {
      stopProcessingIndicators();
    }
    if (target?.id === "certificatePreviewModal") {
      const previewFrame = target.querySelector("[data-certificate-frame]");
      const previewImage = target.querySelector("[data-certificate-image]");
      if (previewFrame) {
        previewFrame.src = "";
        previewFrame.classList.add("d-none");
      }
      if (previewImage) {
        previewImage.src = "";
        previewImage.classList.add("d-none");
      }
    }
  });

  const searchInput = document.querySelector("[data-athlete-surname-filter]");
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      const q = searchInput.value.trim().toLowerCase();
      const items = document.querySelectorAll("[data-athlete-item]");
      items.forEach((item) => {
        const surname = (item.dataset.surname || "").toLowerCase();
        const visible = !q || surname.includes(q);
        item.classList.toggle("d-none", !visible);
      });
      document.querySelectorAll("[data-athlete-section]").forEach((section) => {
        const hasVisible = section.querySelector("[data-athlete-item]:not(.d-none)");
        section.classList.toggle("d-none", !hasVisible);
      });
    });
  }
})();
