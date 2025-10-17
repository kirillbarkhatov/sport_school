document.addEventListener("DOMContentLoaded", () => {
  const modalElement = document.getElementById("familyMemberCreateModal");
  const openButtons = document.querySelectorAll("[data-action='open-add-member']");

  if (!modalElement || !openButtons.length) {
    return;
  }

  const modal = new bootstrap.Modal(modalElement);
  const form = modalElement.querySelector("[data-member-create-form]");
  const familySelect = form.querySelector('[data-role="family-select"]');
  const relationSelect = form.querySelector('[data-role="relation-select"]');
  const feedbackBox = modalElement.querySelector("[data-form-feedback]");
  const submitButton = form.querySelector('button[type="submit"]');
  const selectedFamilyContainer = modalElement.querySelector(
    "[data-selected-family-container]"
  );
  const selectedFamilyLabel = modalElement.querySelector(
    "[data-selected-family]"
  );

  const resetForm = () => {
    form.reset();
    if (relationSelect) {
      relationSelect.disabled = !familySelect.value;
    }
    if (feedbackBox) {
      feedbackBox.classList.add("d-none");
      feedbackBox.textContent = "";
    }
    if (selectedFamilyContainer) {
      selectedFamilyContainer.classList.add("d-none");
      if (selectedFamilyLabel) {
        selectedFamilyLabel.textContent = "не выбрана";
      }
    }
  };

  const setSelectedFamily = (familyId, familyName) => {
    if (!familySelect) {
      return;
    }
    familySelect.value = familyId || "";
    if (relationSelect) {
      relationSelect.disabled = !familySelect.value;
      relationSelect.value = "";
    }
    if (selectedFamilyLabel) {
      selectedFamilyLabel.textContent = familyName || "не выбрана";
    }
    if (selectedFamilyContainer) {
      if (familyId) {
        selectedFamilyContainer.classList.remove("d-none");
      } else {
        selectedFamilyContainer.classList.add("d-none");
      }
    }
  };

  openButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const familyId = button.dataset.familyId || "";
      const familyName = button.dataset.familyName || "не выбрана";
      setSelectedFamily(familyId, familyName);
      if (feedbackBox) {
        feedbackBox.classList.add("d-none");
        feedbackBox.textContent = "";
      }
      modal.show();
    });
  });

  familySelect.addEventListener("change", () => {
    if (relationSelect) {
      relationSelect.disabled = !familySelect.value;
      relationSelect.value = "";
    }
    if (familySelect.value && selectedFamilyLabel) {
      const option =
        familySelect.options[familySelect.selectedIndex]?.textContent || "";
      selectedFamilyLabel.textContent = option || "не выбрана";
      selectedFamilyContainer?.classList.remove("d-none");
    } else {
      selectedFamilyLabel.textContent = "не выбрана";
      selectedFamilyContainer?.classList.add("d-none");
    }
  });

  modalElement.addEventListener("hidden.bs.modal", () => {
    resetForm();
  });

  const showFeedback = (messages, tone = "danger") => {
    if (!feedbackBox) {
      if (Array.isArray(messages)) {
        window.showToast?.(messages.join(". "), tone);
      } else if (messages) {
        window.showToast?.(messages, tone);
      }
      return;
    }
    const text = Array.isArray(messages) ? messages.join(" ") : messages;
    feedbackBox.textContent = text || "";
    feedbackBox.classList.toggle("d-none", !text);
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showFeedback("");
    submitButton.disabled = true;

    const formData = new FormData(form);

    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: formData,
      });
      const payload = await response.json();

      if (!response.ok || !payload.success) {
        showFeedback(
          payload?.errors || ["Не удалось добавить участника. Попробуйте ещё раз."],
          "danger"
        );
        return;
      }

      const familyId = payload.member?.family_id;
      if (familyId) {
        const targetModal = document.getElementById(`familyModal${familyId}`);
        const memberList = targetModal?.querySelector(
          "[data-family-member-list]"
        );
        if (memberList && payload.member.html) {
          const emptyStub = memberList.querySelector("[data-family-empty]");
          if (emptyStub) {
            emptyStub.remove();
          }
          memberList.insertAdjacentHTML("beforeend", payload.member.html);
        }
        if (payload.contact_person) {
          const contactDisplay = targetModal?.querySelector(
            `[data-contact-display][data-family-id="${familyId}"]`
          );
          if (contactDisplay) {
            contactDisplay.textContent = payload.contact_person;
          }
        }
      }

      modal.hide();
      window.showToast?.(payload.message || "Участник добавлен.");
    } catch (error) {
      console.error("Family member create failed:", error);
      showFeedback(
        "Произошла ошибка при добавлении. Проверьте соединение и повторите попытку.",
        "danger"
      );
    } finally {
      submitButton.disabled = false;
    }
  });
});
