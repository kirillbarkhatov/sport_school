document.addEventListener("DOMContentLoaded", () => {
  const forms = document.querySelectorAll(".family-add-member-form");
  if (!forms.length) {
    return;
  }

  const createFeedbackUpdater = (element) => (message, type = "muted") => {
    if (!element) {
      return;
    }
    element.textContent = message || "";
    element.className = `small text-${type}`;
  };

  const updateMemberList = (container, memberPayload) => {
    if (!container || !memberPayload) {
      return;
    }
    const emptyStub = container.querySelector("[data-family-empty]");
    if (emptyStub) {
      emptyStub.remove();
    }
    const existing = container.querySelector(
      `#family-member-${memberPayload.id}`
    );
    if (existing) {
      existing.outerHTML = memberPayload.html;
    } else {
      container.insertAdjacentHTML("beforeend", memberPayload.html);
    }
  };

  forms.forEach((form) => {
    const cardContainer = form.closest("[data-family-card]");
    const memberList = cardContainer?.querySelector("[data-family-member-list]");
    const feedbackEl = form.querySelector("[data-form-feedback]");
    const feedback = createFeedbackUpdater(feedbackEl);
    const personSelect = form.querySelector('select[name="person_id"]');

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      feedback("");

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
            "Не удалось добавить участника. Попробуйте ещё раз.";
          feedback(errorMessage, "danger");
          return;
        }

        updateMemberList(memberList, payload.member);

        if (payload.member?.created && personSelect) {
          const selectedValue = formData.get("person_id");
          if (selectedValue) {
            const optionToRemove = personSelect.querySelector(
              `option[value="${CSS.escape(selectedValue)}"]`
            );
            if (optionToRemove) {
              optionToRemove.remove();
            }
          }
        }

        form.reset();
        feedback(payload.message || "Член семьи добавлен.", "success");
      } catch (error) {
        console.error("Family member add failed:", error);
        feedback(
          "Произошла ошибка при добавлении. Проверьте соединение и повторите попытку.",
          "danger"
        );
      }
    });
  });
});
