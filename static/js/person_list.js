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

  const setFeedback = (element, message, type = "muted") => {
    if (!element) {
      return;
    }
    element.textContent = message || "";
    element.className = `align-self-center small text-${type}`;
  };

  const familyForms = document.querySelectorAll(".person-family-form");

  familyForms.forEach((form) => {
    const feedbackEl = form.querySelector("[data-form-feedback]");
    const familySelect = form.querySelector('select[name="family_id"]');
    const relationSelect = form.querySelector('select[name="relation"]');
    const personId = form.dataset.personId;
    const personSurname = form.dataset.personSurname || "";

    if (!familySelect || !relationSelect) {
      return;
    }

    reorderFamilyOptions(familySelect, personSurname);

    const statusElements = document.querySelectorAll(
      `[data-family-status][data-person-id="${personId}"]`
    );

    const toggleRelationState = () => {
      if (!familySelect.value) {
        relationSelect.value = "";
        relationSelect.disabled = true;
      } else {
        relationSelect.disabled = false;
      }
    };

    toggleRelationState();
    familySelect.addEventListener("change", toggleRelationState);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      setFeedback(feedbackEl, "");

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
          setFeedback(feedbackEl, errorMessage, "danger");
          return;
        }

        if (payload.membership) {
          statusElements.forEach((element) => {
            element.textContent = `${payload.membership.family_name} — ${payload.membership.relation_display}`;
          });
          if (payload.membership.relation) {
            relationSelect.value = payload.membership.relation;
          }
        } else {
          statusElements.forEach((element) => {
            element.textContent = "Семья не указана";
          });
        }

        toggleRelationState();
        setFeedback(feedbackEl, payload.message || "Связь обновлена.", "success");
      } catch (error) {
        console.error("Assign family failed:", error);
        setFeedback(
          feedbackEl,
          "Произошла ошибка при обновлении. Повторите попытку позже.",
          "danger"
        );
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
          alert(errorMessage);
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
      } catch (error) {
        console.error("Toggle athlete failed:", error);
        alert("Не удалось сменить статус. Проверьте соединение и повторите попытку.");
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
