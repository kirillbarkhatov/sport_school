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
    const relationDisplay = displayContainer?.querySelector(
      ".person-relation-display"
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

    relationDisplay?.addEventListener("click", () => openEditor("relation"));
    relationDisplay?.addEventListener("keypress", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openEditor("relation");
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
          if (relationDisplay) {
            relationDisplay.textContent = `Родство: ${membership.relation_display}`;
          }
          familySelect.value = originalFamily;
          relationSelect.value = originalRelation;
        } else {
          originalFamily = "";
          originalRelation = "";
          familySelect.value = "";
          relationSelect.value = "";
          if (familyDisplay) {
            familyDisplay.textContent = "Семья: не указана";
          }
          if (relationDisplay) {
            relationDisplay.textContent = "Родство: не указано";
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
          statusBadge.textContent = "Спортсмен";
          statusBadge.classList.remove("text-bg-secondary");
          statusBadge.classList.add("text-bg-success");
          toggleButton.textContent = "Сделать членом семьи";
          toggleButton.classList.remove("btn-outline-success");
          toggleButton.classList.add("btn-outline-secondary");
        } else {
          statusBadge.textContent = "Член семьи";
          statusBadge.classList.remove("text-bg-success");
          statusBadge.classList.add("text-bg-secondary");
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
});
