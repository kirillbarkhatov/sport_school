document.addEventListener("DOMContentLoaded", () => {
  const container = document.querySelector("[data-family-inline]");
  if (!container) {
    return;
  }

  const updateUrl = container.dataset.updateUrl;

  const getCsrfToken = () => {
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  };

  const showToast = (message, tone = "success") => {
    if (window.showToast) {
      window.showToast(message, tone);
    }
  };

  const editors = new Map();

  container.querySelectorAll("[data-family-editor]").forEach((editor) => {
    const field = editor.dataset.familyEditor;
    const input = editor.querySelector("[data-editor-input]");
    const saveButton = editor.querySelector('[data-editor-action="save"]');
    const cancelButton = editor.querySelector('[data-editor-action="cancel"]');
    const feedback = editor.querySelector("[data-editor-feedback]");
    editors.set(field, { editor, input, saveButton, cancelButton, feedback });
    if (input) {
      editor.dataset.originalValue = input.value || "";
    }
  });

  const displayMap = new Map();
  container.querySelectorAll("[data-family-field]").forEach((element) => {
    const key = element.dataset.familyField;
    displayMap.set(key, element);
  });

  const closeEditor = (field) => {
    const config = editors.get(field);
    if (!config) {
      return;
    }
    const { editor, input, feedback } = config;
    editor.classList.add("d-none");
    if (feedback) {
      feedback.classList.add("d-none");
      feedback.textContent = "";
    }
    if (input) {
      input.value = editor.dataset.originalValue || "";
    }
  };

  const openEditor = (field) => {
    const config = editors.get(field);
    if (!config) {
      return;
    }
    const { editor, input } = config;
    editor.classList.remove("d-none");
    if (input) {
      input.focus();
      if (input.tagName === "INPUT") {
        input.select();
      }
    }
  };

  const toggleButtons = container.querySelectorAll(
    "[data-family-edit-trigger]"
  );
  toggleButtons.forEach((button) => {
    const field = button.dataset.familyEditTrigger;
    button.addEventListener("click", () => openEditor(field));
  });

  editors.forEach(({ editor, input, saveButton, cancelButton, feedback }, field) => {
    if (cancelButton) {
      cancelButton.addEventListener("click", () => closeEditor(field));
    }

    const handleResponse = (payload, committedValue) => {
      const newValue = committedValue ?? "";
      if (input) {
        if (input.tagName === "SELECT") {
          input.value = newValue;
        } else {
          input.value = newValue;
        }
      }
      switch (field) {
        case "family_name": {
          const display = displayMap.get("family_name-display");
          if (display) {
            display.textContent = payload.family_name || "Без названия";
          }
          editor.dataset.originalValue = newValue;
          if (payload.family_name) {
            document.title = `${payload.family_name} — семья`;
          } else {
            document.title = "Без названия — семья";
          }
          break;
        }
        case "contact_person": {
          const display = displayMap.get("contact_person-display");
          if (display) {
            display.textContent = payload.contact_person || "Не указано";
          }
          editor.dataset.originalValue = newValue;
          break;
        }
        case "status": {
          const display = displayMap.get("status-display");
          if (display) {
            display.textContent = payload.status_display || display.textContent;
          }
          editor.dataset.originalValue = newValue;
          break;
        }
        default:
          break;
      }
      closeEditor(field);
      showToast(payload.message || "Изменения сохранены.");
    };

    if (saveButton) {
      saveButton.addEventListener("click", async () => {
        if (!input) {
          return;
        }
        if (feedback) {
          feedback.classList.add("d-none");
          feedback.textContent = "";
        }
        saveButton.disabled = true;

        const value =
          input.tagName === "SELECT"
            ? input.options[input.selectedIndex]?.value || ""
            : input.value.trim();

        const formData = new FormData();
        formData.append("field", field);
        formData.append("value", value);

        try {
          const response = await fetch(updateUrl, {
            method: "POST",
            headers: {
              "X-CSRFToken": getCsrfToken(),
              "X-Requested-With": "XMLHttpRequest",
            },
            body: formData,
          });
          const payload = await response.json();

          if (!response.ok || !payload.success) {
            const message =
              payload?.errors?.join(". ") ||
              "Не удалось сохранить изменения. Повторите попытку позже.";
            if (feedback) {
              feedback.textContent = message;
              feedback.classList.remove("d-none");
            } else {
              showToast(message, "danger");
            }
            return;
          }

          handleResponse(payload, value);
        } catch (error) {
          console.error("Family inline update failed:", error);
          const message =
            "Произошла ошибка при сохранении. Проверьте соединение и повторите попытку.";
          if (feedback) {
            feedback.textContent = message;
            feedback.classList.remove("d-none");
          } else {
            showToast(message, "danger");
          }
        } finally {
          saveButton.disabled = false;
        }
      });
    }
  });
});
