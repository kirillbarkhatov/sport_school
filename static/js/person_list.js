document.addEventListener("DOMContentLoaded", () => {
  const normalizePrefix = (value = "") =>
    value
      .toString()
      .trim()
      .toLowerCase()
      .replace(/[^a-zа-яё]/g, "")
      .slice(0, 3);

  const reorderFamilyOptions = (select, surname) => {
    if (!select) {
      return;
    }

    const surnamePrefix = normalizePrefix(surname);
    if (!surnamePrefix) {
      return;
    }

    const options = Array.from(select.options).slice(1);
    options.sort((optionA, optionB) => {
      const prefixA = normalizePrefix(optionA.textContent);
      const prefixB = normalizePrefix(optionB.textContent);
      const rankA = prefixA === surnamePrefix ? 0 : 1;
      const rankB = prefixB === surnamePrefix ? 0 : 1;
      if (rankA !== rankB) {
        return rankA - rankB;
      }
      return optionA.textContent.localeCompare(optionB.textContent, "ru", {
        sensitivity: "base",
      });
    });

    options.forEach((option) => select.appendChild(option));
  };

  const setFeedback = (element, message) => {
    if (!element) {
      return;
    }
    if (!message) {
      element.textContent = "";
      element.classList.add("d-none");
      return;
    }
    element.textContent = message;
    element.classList.remove("d-none");
  };

  const showToast = (message, variant = "success") => {
    if (window.showToast) {
      window.showToast(message, variant);
    }
  };

  const setStatusBadgeState = (
    personId,
    { isAthlete, relationLabel = "", relationCode = "" } = {}
  ) => {
    const badge = document.querySelector(
      `[data-person-status-badge][data-person-id="${personId}"]`
    );
    if (!badge) {
      return;
    }
    if (isAthlete) {
      badge.textContent = "Спортсмен";
      badge.classList.add("text-bg-success");
      badge.classList.remove("text-bg-secondary");
      delete badge.dataset.relationCode;
      delete badge.dataset.relationDisplay;
      return;
    }
    const trimmedLabel = relationLabel.trim();
    badge.textContent = trimmedLabel
      ? `Член семьи - ${trimmedLabel}`
      : "Член семьи";
    badge.classList.add("text-bg-secondary");
    badge.classList.remove("text-bg-success");
    if (relationCode) {
      badge.dataset.relationCode = relationCode;
    } else {
      delete badge.dataset.relationCode;
    }
    if (trimmedLabel) {
      badge.dataset.relationDisplay = trimmedLabel;
    } else {
      delete badge.dataset.relationDisplay;
    }
  };

  const familyForms = document.querySelectorAll(".person-family-form");

  familyForms.forEach((form) => {
    const personId = form.dataset.personId;
    const personSurname = form.dataset.personSurname || "";
    const familySelect = form.querySelector('[data-role="family-select"]');
    const relationSelect = form.querySelector('[data-role="relation-select"]');
    const saveButton = form.querySelector('[data-role="family-save"]');
    const feedbackEl = form.querySelector("[data-form-feedback]");
    const displayContainer = document.querySelector(
      `[data-family-display-container][data-person-id="${personId}"]`
    );
    const familyDisplay = displayContainer?.querySelector(
      ".person-family-display"
    );
    const statusBadge = document.querySelector(
      `[data-person-status-badge][data-person-id="${personId}"]`
    );

    if (!familySelect || !relationSelect || !displayContainer) {
      return;
    }

    reorderFamilyOptions(familySelect, personSurname);

    let originalFamily = familySelect.value || "";
    let originalRelation = relationSelect.value || "";

    const toggleRelationState = () => {
      if (!familySelect.value) {
        relationSelect.value = "";
        relationSelect.disabled = true;
      } else {
        relationSelect.disabled = false;
      }
    };

    const updateSaveVisibility = () => {
      const hasChanges =
        familySelect.value !== originalFamily ||
        relationSelect.value !== originalRelation;
      if (hasChanges) {
        saveButton.classList.remove("d-none");
      } else {
        saveButton.classList.add("d-none");
      }
    };

    toggleRelationState();
    updateSaveVisibility();

    const closeEditor = () => {
      form.classList.add("d-none");
      displayContainer.classList.remove("d-none");
      setFeedback(feedbackEl, "");
      familySelect.value = originalFamily;
      relationSelect.value = originalRelation;
      toggleRelationState();
      saveButton.classList.add("d-none");
    };

    const openEditor = (focusTarget) => {
      displayContainer.classList.add("d-none");
      form.classList.remove("d-none");
      toggleRelationState();
      updateSaveVisibility();
      if (focusTarget === "relation" && !familySelect.value) {
        familySelect.focus();
        return;
      }
      if (focusTarget === "relation") {
        relationSelect.focus();
      } else {
        familySelect.focus();
      }
    };

    familyDisplay?.addEventListener("click", () => openEditor("family"));
    familyDisplay?.addEventListener("keypress", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openEditor("family");
      }
    });

    familySelect.addEventListener("change", () => {
      toggleRelationState();
      updateSaveVisibility();
    });
    relationSelect.addEventListener("change", updateSaveVisibility);

    form.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeEditor();
      }
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      setFeedback(feedbackEl, "");
      if (saveButton) {
        saveButton.disabled = true;
      }

      const formData = new FormData(form);
      try {
        const response = await fetch(form.action, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
          },
          body: formData,
        });
        const payload = await response.json();

        if (!response.ok || !payload.success) {
          const errorMessage =
            payload?.errors?.join(". ") ||
            "Не удалось обновить связь с семьёй.";
          setFeedback(feedbackEl, errorMessage);
          return;
        }

        const membership = payload.membership;
        if (membership) {
          originalFamily = membership.family_id.toString();
          originalRelation = membership.relation || "";
          if (familyDisplay) {
            familyDisplay.textContent = `Семья: ${membership.family_name}`;
          }
          familySelect.value = originalFamily;
          relationSelect.value = originalRelation;
          if (
            statusBadge &&
            !statusBadge.classList.contains("text-bg-success")
          ) {
            setStatusBadgeState(personId, {
              isAthlete: false,
              relationLabel: membership.relation_display || "",
              relationCode: membership.relation || "",
            });
          }
        } else {
          originalFamily = "";
          originalRelation = "";
          familySelect.value = "";
          relationSelect.value = "";
          if (familyDisplay) {
            familyDisplay.textContent = "Семья: не указана";
          }
          if (
            statusBadge &&
            !statusBadge.classList.contains("text-bg-success")
          ) {
            setStatusBadgeState(personId, { isAthlete: false });
          }
        }

        toggleRelationState();
        closeEditor();
        showToast(payload.message || "Связь обновлена.");
      } catch (error) {
        console.error("Assign family failed:", error);
        setFeedback(
          feedbackEl,
          "Произошла ошибка при обновлении. Повторите попытку позже."
        );
      } finally {
        if (saveButton) {
          saveButton.disabled = false;
        }
      }
    });
  });

  const statusForms = document.querySelectorAll(".person-status-toggle");

  statusForms.forEach((form) => {
    const personId = form.dataset.personId;
    const statusBadge = document.querySelector(
      `[data-person-status-badge][data-person-id="${personId}"]`
    );
    const toggleButton = form.querySelector(
      '[data-role="status-toggle-button"]'
    );

    if (!statusBadge || !toggleButton) {
      return;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      toggleButton.disabled = true;
      const formData = new FormData(form);

      try {
        const response = await fetch(form.action, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
          },
          body: formData,
        });
        const payload = await response.json();

        if (!response.ok || !payload.success) {
          const errorMessage =
            payload?.errors?.join(". ") ||
            "Не удалось обновить статус. Попробуйте позже.";
          console.error("Toggle athlete failed:", errorMessage);
          showToast(errorMessage, "danger");
          return;
        }

        if (payload.is_athlete) {
          setStatusBadgeState(personId, { isAthlete: true });
          toggleButton.textContent = "Сделать членом семьи";
          toggleButton.classList.remove("btn-outline-success");
          toggleButton.classList.add("btn-outline-secondary");
        } else {
          let relationLabel = "";
          let relationCode = "";
          const familyForm = document.querySelector(
            `.person-family-form[data-person-id="${personId}"]`
          );
          const relationSelect = familyForm?.querySelector(
            '[data-role="relation-select"]'
          );
          if (relationSelect && relationSelect.value) {
            relationCode = relationSelect.value;
            const option =
              relationSelect.options[relationSelect.selectedIndex];
            relationLabel = option ? option.textContent.trim() : "";
          } else {
            relationLabel = statusBadge.dataset.relationDisplay || "";
            relationCode = statusBadge.dataset.relationCode || "";
          }
          setStatusBadgeState(personId, {
            isAthlete: false,
            relationLabel,
            relationCode,
          });
          toggleButton.textContent = "Сделать спортсменом";
          toggleButton.classList.remove("btn-outline-secondary");
          toggleButton.classList.add("btn-outline-success");
        }

        showToast(payload.message || "Статус обновлён.");
      } catch (error) {
        console.error("Toggle athlete failed:", error);
        showToast(
          "Не удалось сменить статус. Проверьте соединение и повторите попытку.",
          "danger"
        );
      } finally {
        toggleButton.disabled = false;
      }
    });
  });

  if (typeof bootstrap !== "undefined" && bootstrap.Tooltip) {
    const tooltipElements = [].slice.call(
      document.querySelectorAll('[data-bs-toggle="tooltip"]')
    );
    tooltipElements.forEach((element) => new bootstrap.Tooltip(element));
  }

  const personCreateModal = document.getElementById("personCreateModal");
  if (personCreateModal) {
    const createForm = personCreateModal.querySelector(
      '[data-role="person-create-form"]'
    );
    const familySelect = createForm?.querySelector(
      '[data-role="modal-family-select"]'
    );
    const relationSelect = createForm?.querySelector(
      '[data-role="modal-relation-select"]'
    );
    const contactCheckbox = createForm?.querySelector(
      '[data-role="modal-family-contact"]'
    );
    const errorsContainer = createForm?.querySelector(
      '[data-role="person-form-errors"]'
    );
    const submitButton = createForm?.querySelector(
      '[data-role="modal-submit-button"]'
    );

    const clearErrors = () => {
      if (!createForm) {
        return;
      }
      if (errorsContainer) {
        errorsContainer.classList.add("d-none");
        errorsContainer.textContent = "";
      }
      createForm
        .querySelectorAll(".is-invalid")
        .forEach((element) => element.classList.remove("is-invalid"));
      createForm
        .querySelectorAll("[data-field-error]")
        .forEach((element) => {
          element.classList.add("d-none");
          element.textContent = "";
        });
    };

    const toggleModalRelationState = () => {
      if (!familySelect || !relationSelect || !contactCheckbox) {
        return;
      }
      if (!familySelect.value) {
        relationSelect.value = "";
        relationSelect.disabled = true;
        relationSelect.classList.remove("is-invalid");
        contactCheckbox.checked = false;
        contactCheckbox.disabled = true;
        contactCheckbox.classList.remove("is-invalid");
      } else {
        relationSelect.disabled = false;
        relationSelect.classList.remove("is-invalid");
        contactCheckbox.disabled = false;
        contactCheckbox.classList.remove("is-invalid");
      }
    };

    const renderErrors = (errors = {}) => {
      if (!createForm) {
        return;
      }
      const generalMessages = [];
      Object.entries(errors).forEach(([field, messages]) => {
        const messageText = Array.isArray(messages)
          ? messages.join(" ")
          : String(messages || "");
        if (!messageText) {
          return;
        }
        if (field === "__all__") {
          generalMessages.push(messageText);
          return;
        }
        const fieldError = createForm.querySelector(
          `[data-field-error="${field}"]`
        );
        const inputs = createForm.querySelectorAll(`[name="${field}"]`);
        if (!fieldError || inputs.length === 0) {
          generalMessages.push(messageText);
          return;
        }
        fieldError.textContent = messageText;
        fieldError.classList.remove("d-none");
        inputs.forEach((input) => input.classList.add("is-invalid"));
      });
      if (generalMessages.length && errorsContainer) {
        errorsContainer.textContent = generalMessages.join(" ");
        errorsContainer.classList.remove("d-none");
      }
    };

    familySelect?.addEventListener("change", () => {
      toggleModalRelationState();
      if (!createForm) {
        return;
      }
      const familyError = createForm.querySelector(
        '[data-field-error="family_id"]'
      );
      if (familyError) {
        familyError.classList.add("d-none");
        familyError.textContent = "";
      }
      familySelect.classList.remove("is-invalid");
      if (relationSelect) {
        relationSelect.classList.remove("is-invalid");
        const relationError = createForm.querySelector(
          '[data-field-error="relation"]'
        );
        if (relationError) {
          relationError.classList.add("d-none");
          relationError.textContent = "";
        }
      }
    });

    personCreateModal.addEventListener("show.bs.modal", () => {
      clearErrors();
      toggleModalRelationState();
    });

    personCreateModal.addEventListener("hidden.bs.modal", () => {
      createForm?.reset();
      clearErrors();
      toggleModalRelationState();
    });

    createForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!createForm) {
        return;
      }
      clearErrors();
      if (submitButton) {
        submitButton.disabled = true;
      }
      const formData = new FormData(createForm);
      try {
        const response = await fetch(createForm.action, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
          },
          body: formData,
        });
        const payload = await response.json();
        if (!response.ok || !payload?.success) {
          renderErrors(payload?.errors || {});
          const fallbackMessage =
            payload?.message ||
            "Не удалось добавить участника. Проверьте данные и попробуйте снова.";
          if (errorsContainer && errorsContainer.classList.contains("d-none")) {
            errorsContainer.textContent = fallbackMessage;
            errorsContainer.classList.remove("d-none");
          }
          return;
        }
        const modalInstance = bootstrap.Modal.getOrCreateInstance(
          personCreateModal
        );
        showToast(
          payload.message || "Участник успешно добавлен.",
          "success"
        );
        modalInstance.hide();
        const redirectUrl =
          payload.redirect_url || window.location.href;
        setTimeout(() => {
          window.location.href = redirectUrl;
        }, 200);
      } catch (error) {
        console.error("Create person failed:", error);
        if (errorsContainer) {
          errorsContainer.textContent =
            "Произошла ошибка при добавлении. Повторите попытку позже.";
          errorsContainer.classList.remove("d-none");
        }
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
        }
      }
    });
  }
});
