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

  const collectState = (row) => {
    const birthInput = row.querySelector('[data-field="birth_year"]');
    const levelSelect = row.querySelector('[data-field="level"]');
    const groupSelect = row.querySelector('[data-field="group_ids"]');
    const rankInput = row.querySelector('[data-field="rank"]');
    const certificateInput = row.querySelector(
      '[data-field="medical_certificate"]'
    );
    const commentInput = row.querySelector('[data-field="comment"]');

    const birthValue = birthInput?.value?.trim();
    const birthYear = birthValue ? Number(birthValue) : null;

    let groupIds = [];
    if (groupSelect) {
      if (groupSelect.multiple) {
        groupIds = Array.from(groupSelect.options)
          .filter((option) => option.selected)
          .map((option) => Number(option.value));
      } else {
        const value = groupSelect.value.trim();
        groupIds = value ? [Number(value)] : [];
      }
    }
    groupIds = groupIds.filter((id) => Number.isFinite(id)).sort((a, b) => a - b);

    return {
      birth_year: Number.isFinite(birthYear) ? birthYear : null,
      level: levelSelect ? levelSelect.value : "",
      group_ids: groupIds,
      rank: rankInput ? rankInput.value.trim() : "",
      medical_certificate: certificateInput
        ? certificateInput.value.trim()
        : "",
      comment: commentInput ? commentInput.value.trim() : "",
    };
  };

  const applyState = (row, state) => {
    const birthInput = row.querySelector('[data-field="birth_year"]');
    const levelSelect = row.querySelector('[data-field="level"]');
    const groupSelect = row.querySelector('[data-field="group_ids"]');
    const rankInput = row.querySelector('[data-field="rank"]');
    const certificateInput = row.querySelector(
      '[data-field="medical_certificate"]'
    );
    const commentInput = row.querySelector('[data-field="comment"]');

    if (birthInput) {
      birthInput.value = state.birth_year || "";
    }
    if (levelSelect) {
      levelSelect.value = state.level || "";
    }
    if (groupSelect) {
      if (groupSelect.multiple) {
        const selected = new Set(state.group_ids || []);
        Array.from(groupSelect.options).forEach((option) => {
          option.selected = selected.has(Number(option.value));
        });
      } else {
        const value =
          state.group_ids && state.group_ids.length
            ? String(state.group_ids[0])
            : "";
        groupSelect.value = value;
      }
    }
    if (rankInput) {
      rankInput.value = state.rank || "";
    }
    if (certificateInput) {
      certificateInput.value = state.medical_certificate || "";
    }
    if (commentInput) {
      commentInput.value = state.comment || "";
    }
  };

  const statesEqual = (a, b) => {
    if (!a || !b) {
      return false;
    }
    const sameBirth = a.birth_year === b.birth_year;
    const sameLevel = a.level === b.level;
    const sameRank = a.rank === b.rank;
    const sameCertificate = a.medical_certificate === b.medical_certificate;
    const sameComment = a.comment === b.comment;
    const sameGroups =
      a.group_ids.length === b.group_ids.length &&
      a.group_ids.join(",") === b.group_ids.join(",");
    return (
      sameBirth &&
      sameLevel &&
      sameRank &&
      sameCertificate &&
      sameComment &&
      sameGroups
    );
  };

  const preparePayload = (state) => ({
    birth_year: state.birth_year,
    level: state.level || "",
    group_ids: state.group_ids || [],
    rank: state.rank || "",
    medical_certificate: state.medical_certificate || "",
    comment: state.comment || "",
  });

  const updateInitialState = (row, state) => {
    row.dataset.initialState = JSON.stringify(state);
  };

  const parseInitialState = (row) => {
    try {
      const stored = row.dataset.initialState;
      if (stored) {
        return JSON.parse(stored);
      }
    } catch (error) {
      console.warn("Не удалось разобрать исходное состояние спортсмена", error);
    }
    return collectState(row);
  };

  const setButtonsState = (saveBtn, resetBtn, dirty, loading = false) => {
    if (saveBtn) {
      saveBtn.disabled = !dirty || loading;
      saveBtn.classList.toggle("disabled", loading);
    }
    if (resetBtn) {
      resetBtn.disabled = !dirty || loading;
    }
  };

  const handleRow = (row) => {
    const saveBtn = row.querySelector('[data-action="save"]');
    const resetBtn = row.querySelector('[data-action="reset"]');
    const statusEl = row.querySelector("[data-status]");
    const updateUrl = row.dataset.updateUrl;

    if (!updateUrl) {
      return;
    }

    const inputs = Array.from(
      row.querySelectorAll(
        'input[data-field], select[data-field], textarea[data-field]'
      )
    );

    let initialState = collectState(row);
    updateInitialState(row, initialState);
    let isSubmitting = false;

    const refreshButtons = () => {
      const currentState = collectState(row);
      const dirty = !statesEqual(initialState, currentState);
      setButtonsState(saveBtn, resetBtn, dirty, isSubmitting);
    };

    inputs.forEach((input) => {
      input.addEventListener("change", refreshButtons);
      input.addEventListener("input", refreshButtons);
    });

    resetBtn?.addEventListener("click", () => {
      initialState = parseInitialState(row);
      applyState(row, initialState);
      refreshButtons();
      if (statusEl) {
        statusEl.textContent = "";
      }
    });

    saveBtn?.addEventListener("click", async () => {
      if (isSubmitting) {
        return;
      }
      const currentState = collectState(row);
      isSubmitting = true;
      refreshButtons();
      if (statusEl) {
        statusEl.textContent = "Сохраняем…";
      }

      try {
        const response = await fetch(updateUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken || "",
          },
          body: JSON.stringify(preparePayload(currentState)),
        });

        if (!response.ok) {
          throw new Error(`Ошибка ${response.status}`);
        }

        const data = await response.json();
        if (!data?.success || !data?.athlete) {
          throw new Error("Некорректный ответ сервера");
        }

        const payload = data.athlete;
        const normalizedState = {
          birth_year: payload.birth_year ?? currentState.birth_year ?? null,
          level: payload.level ?? currentState.level ?? "",
          group_ids: Array.isArray(payload.groups)
            ? payload.groups.map((group) => Number(group.id))
            : currentState.group_ids,
          rank: payload.rank ?? "",
          medical_certificate: payload.medical_certificate ?? "",
          comment: payload.comment ?? "",
        };
        normalizedState.group_ids.sort((a, b) => a - b);

        applyState(row, normalizedState);
        initialState = normalizedState;
        updateInitialState(row, normalizedState);
        refreshButtons();

        if (statusEl) {
          statusEl.textContent = "Изменения сохранены";
        }
        showToastSafe("Данные спортсмена обновлены");
      } catch (error) {
        console.error(error);
        if (statusEl) {
          statusEl.textContent = "Не удалось сохранить изменения";
        }
        showToastSafe("Не получилось сохранить изменения", "danger");
      } finally {
        isSubmitting = false;
        refreshButtons();
      }
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    const rows = document.querySelectorAll("[data-athlete-row]");
    rows.forEach(handleRow);
  });
})();
