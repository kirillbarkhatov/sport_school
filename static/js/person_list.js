document.addEventListener("DOMContentLoaded", () => {
  const forms = document.querySelectorAll(".person-family-form");
  if (!forms.length) {
    return;
  }

  const setFeedback = (element, message, type = "muted") => {
    if (!element) {
      return;
    }
    element.textContent = message || "";
    element.className = `align-self-center small text-${type}`;
  };

  forms.forEach((form) => {
    const feedbackEl = form.querySelector("[data-form-feedback]");
    const familySelect = form.querySelector('select[name="family_id"]');
    const relationSelect = form.querySelector('select[name="relation"]');
    const personId = form.dataset.personId;

    if (!familySelect || !relationSelect) {
      return;
    }

    const statusElements = document.querySelectorAll(
      `[data-family-status][data-person-id="${personId}"]`
    );

    const toggleRelationState = () => {
      if (!relationSelect) {
        return;
      }
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
          if (relationSelect && payload.membership.relation) {
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
});
