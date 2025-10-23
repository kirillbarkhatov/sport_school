(() => {
  const getCookie = (name) => {
    const cookieString = document.cookie;
    if (!cookieString) {
      return null;
    }
    const cookies = cookieString.split(";").map((cookie) => cookie.trim());
    for (const cookie of cookies) {
      if (cookie.startsWith(`${name}=`)) {
        return decodeURIComponent(cookie.slice(name.length + 1));
      }
    }
    return null;
  };

  const csrfToken = getCookie("csrftoken");

  const showToastSafe = (message, tone = "success") => {
    if (typeof window.showToast === "function") {
      window.showToast(message, tone);
    }
  };

  const renderMembers = (card, members) => {
    const list = card.querySelector("[data-member-list]");
    const countLabel = card.querySelector("[data-member-count]");
    if (!list) {
      return;
    }
    list.innerHTML = "";

    if (!members.length) {
      const emptyState = document.createElement("li");
      emptyState.className = "list-group-item text-muted";
      emptyState.dataset.emptyState = "true";
      emptyState.textContent = "В группу пока никто не добавлен.";
      list.appendChild(emptyState);
    } else {
      members.forEach((member) => {
        const item = document.createElement("li");
        item.className =
          "list-group-item d-flex justify-content-between align-items-start gap-3";
        item.dataset.memberItem = "true";
        item.dataset.athleteId = member.id;

        const info = document.createElement("div");
        info.innerHTML = `
          <div class="fw-semibold">${member.full_name}</div>
          <div class="small text-muted">
            ${member.level ? `Сезон: ${member.level}` : ""}
            ${member.rank ? ` · Разряд: ${member.rank}` : ""}
          </div>
        `;

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "btn btn-outline-danger btn-sm";
        removeButton.dataset.action = "remove-member";
        removeButton.dataset.athleteId = member.id;
        removeButton.textContent = "Удалить";

        item.appendChild(info);
        item.appendChild(removeButton);
        list.appendChild(item);
      });
    }

    if (countLabel) {
      countLabel.textContent = members.length;
    }
  };

  const syncSelectOptions = (card, memberIds) => {
    const select = card.querySelector("[data-available-select]");
    if (!select) {
      return;
    }
    const memberSet = new Set(memberIds.map(Number));
    Array.from(select.options).forEach((option) => {
      const id = Number(option.value);
      option.disabled = memberSet.has(id);
      if (option.disabled) {
        option.selected = false;
      }
    });
  };

  const filterSelectOptions = (select, query) => {
    const normalized = query.trim().toLowerCase();
    Array.from(select.options).forEach((option) => {
      if (!normalized) {
        option.hidden = false;
        return;
      }
      option.hidden = !option.textContent.toLowerCase().includes(normalized);
    });
  };

  const postMembershipUpdate = async (card, payload) => {
    const updateUrl = card.dataset.updateUrl;
    if (!updateUrl) {
      throw new Error("Не задан адрес обновления состава группы");
    }
    const response = await fetch(updateUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken || "",
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(`Ошибка ${response.status}`);
    }
    const data = await response.json();
    if (!data?.success || !Array.isArray(data.members)) {
      throw new Error("Некорректный ответ сервера");
    }
    return data.members;
  };

  const handleCard = (card) => {
    const select = card.querySelector("[data-available-select]");
    const searchInput = card.querySelector("[data-available-search]");
    const addButton = card.querySelector('[data-action="add-athletes"]');
    const clearButton = card.querySelector('[data-action="clear-selection"]');
    const feedback = card.querySelector("[data-feedback]");

    if (searchInput && select) {
      searchInput.addEventListener("input", () =>
        filterSelectOptions(select, searchInput.value)
      );
    }

    clearButton?.addEventListener("click", () => {
      if (select) {
        Array.from(select.options).forEach((option) => {
          option.selected = false;
        });
      }
      if (feedback) {
        feedback.textContent = "";
      }
    });

    const setFeedback = (message, tone = "muted") => {
      if (!feedback) {
        return;
      }
      feedback.textContent = message;
      feedback.className = `small text-${tone} mt-2`;
    };

    addButton?.addEventListener("click", async () => {
      if (!select) {
        return;
      }
      const selectedOptions = Array.from(select.selectedOptions).filter(
        (option) => !option.disabled
      );
      if (!selectedOptions.length) {
        setFeedback("Выберите спортсменов для добавления.", "warning");
        return;
      }
      const athleteIds = selectedOptions.map((option) => Number(option.value));
      addButton.disabled = true;
      setFeedback("Добавляем спортсменов…", "muted");
      try {
        const members = await postMembershipUpdate(card, {
          action: "add",
          athlete_ids: athleteIds,
        });
        renderMembers(card, members);
        syncSelectOptions(
          card,
          members.map((member) => Number(member.id))
        );
        Array.from(select.options).forEach((option) => {
          option.selected = false;
        });
        setFeedback("Спортсмены добавлены.", "success");
        showToastSafe("Состав группы обновлён");
      } catch (error) {
        console.error(error);
        setFeedback("Не удалось обновить состав группы.", "danger");
        showToastSafe("Не удалось обновить состав группы", "danger");
      } finally {
        addButton.disabled = false;
      }
    });

    card.addEventListener("click", async (event) => {
      const target = event.target.closest('[data-action="remove-member"]');
      if (!target) {
        return;
      }
      const athleteId = Number(target.dataset.athleteId);
      if (!Number.isFinite(athleteId)) {
        return;
      }
      target.disabled = true;
      setFeedback("Удаляем спортсмена…", "muted");
      try {
        const members = await postMembershipUpdate(card, {
          action: "remove",
          athlete_ids: [athleteId],
        });
        renderMembers(card, members);
        syncSelectOptions(
          card,
          members.map((member) => Number(member.id))
        );
        setFeedback("Спортсмен удалён из группы.", "success");
        showToastSafe("Состав группы обновлён");
      } catch (error) {
        console.error(error);
        setFeedback("Не удалось удалить спортсмена.", "danger");
        showToastSafe("Не удалось удалить спортсмена", "danger");
      } finally {
        target.disabled = false;
      }
    });

    // Изначально синхронизируем доступные варианты.
    const initialMembers = Array.from(
      card.querySelectorAll("[data-member-item]")
    )
      .map((item) => Number(item.dataset.athleteId))
      .filter((id) => Number.isFinite(id));
    syncSelectOptions(card, initialMembers);
  };

  document.addEventListener("DOMContentLoaded", () => {
    const cards = document.querySelectorAll("[data-group-card]");
    cards.forEach(handleCard);
  });
})();
