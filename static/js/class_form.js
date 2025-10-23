(() => {
  function parseJsonScript(id) {
    const script = document.getElementById(id);
    if (!script) {
      return null;
    }
    try {
      return JSON.parse(script.textContent);
    } catch (error) {
      console.error(`Не удалось разобрать JSON из #${id}`, error);
      return null;
    }
  }

  function setHintVisibility(element, visible) {
    if (!element) {
      return;
    }
    element.classList.toggle("d-none", !visible);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector("[data-training-form]");
    if (!form) {
      return;
    }

    const config = parseJsonScript("training-form-config");
    if (!config) {
      return;
    }

    const groupMap = parseJsonScript("group-athletes-map") || {};

    const locationSelect = form.querySelector("#id_location");
    const trainingSelect = form.querySelector("#id_training_type");
    const typeSelect = form.querySelector("#id_type");
    const groupSelect = form.querySelector("#id_group");
    const equipmentWrapper = form.querySelector("[data-equipment-select]");
    const equipmentSelect = equipmentWrapper
      ? equipmentWrapper.querySelector("select")
      : null;
    const equipmentMenu = form.querySelector("[data-equipment-menu]");
    const equipmentLabel = form.querySelector("[data-equipment-label]");
    const equipmentToggle = form.querySelector("[data-equipment-toggle]");
    const trainingHintLocked = form.querySelector("[data-training-hint='locked']");
    const equipmentHintLocation = form.querySelector(
      "[data-equipment-hint='location']"
    );
    const equipmentHintTraining = form.querySelector(
      "[data-equipment-hint='training']"
    );
    const groupOptionalHint = form.querySelector("[data-group-optional-hint]");

    if (
      !locationSelect ||
      !trainingSelect ||
      !equipmentSelect ||
      !equipmentMenu ||
      !equipmentLabel ||
      !equipmentToggle
    ) {
      return;
    }

    const choicesConfig = config.choices || {};
    const allowedTrainingMap = config.allowed_training || {};
    const allowedEquipmentMap = config.allowed_equipment || {};
    const defaultsMap = config.defaults || {};

    const equipmentChoices = choicesConfig.equipment || [];
    const equipmentLabelMap = new Map(
      equipmentChoices.map((choice) => [choice.value, choice.label])
    );
    const trainingChoices = choicesConfig.training || [];
    const trainingChoiceMap = new Map(
      trainingChoices.map((choice) => [choice.value, choice.label])
    );

    const athleteContainer = form.querySelector("[data-athlete-list]");
    const athleteCheckboxMap = new Map();
    if (athleteContainer) {
      const checkboxes = athleteContainer.querySelectorAll("input[type='checkbox']");
      checkboxes.forEach((checkbox) => {
        athleteCheckboxMap.set(checkbox.value, checkbox);
      });
    }

    let equipmentSelection = new Set(
      Array.from(equipmentSelect.selectedOptions || []).map((option) => option.value)
    );
    let equipmentModifiedByUser = equipmentSelection.size > 0;
    let currentEquipmentChoices = [];

    function updateEquipmentLabel() {
      if (!equipmentLabel) {
        return;
      }
      if (!equipmentSelection.size) {
        equipmentLabel.textContent = "Выберите экипировку";
        return;
      }
      const orderedValues = currentEquipmentChoices
        .filter((choice) => equipmentSelection.has(choice.value))
        .map((choice) => choice.label);
      if (!orderedValues.length) {
        orderedValues.push(
          ...Array.from(equipmentSelection, (value) => equipmentLabelMap.get(value) || value)
        );
      }
      equipmentLabel.textContent = orderedValues.join(", ");
    }

    function renderEquipmentSelect(choices) {
      equipmentSelect.innerHTML = "";
      const selectedValues = new Set(equipmentSelection);
      choices.forEach((choice) => {
        const option = document.createElement("option");
        option.value = choice.value;
        option.textContent = choice.label;
        option.selected = selectedValues.has(choice.value);
        equipmentSelect.appendChild(option);
      });
    }

    equipmentMenu.addEventListener("click", (event) => {
      event.stopPropagation();
    });

    function renderEquipmentMenu(choices) {
      equipmentMenu.innerHTML = "";
      if (!choices.length) {
        const emptyItem = document.createElement("div");
        emptyItem.className = "dropdown-item text-muted small";
        emptyItem.textContent = "Подходящих вариантов нет";
        equipmentMenu.appendChild(emptyItem);
        return;
      }

      choices.forEach((choice) => {
        const item = document.createElement("label");
        item.className = "dropdown-item form-check d-flex align-items-center gap-2";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "form-check-input";
        checkbox.value = choice.value;
        checkbox.checked = equipmentSelection.has(choice.value);
        checkbox.addEventListener("change", (event) => {
          event.stopPropagation();
          if (checkbox.checked) {
            equipmentSelection.add(choice.value);
          } else {
            equipmentSelection.delete(choice.value);
          }
          equipmentModifiedByUser = true;
          renderEquipmentSelect(currentEquipmentChoices);
          updateEquipmentLabel();
        });
        item.appendChild(checkbox);

        const labelSpan = document.createElement("span");
        labelSpan.textContent = choice.label;
        item.appendChild(labelSpan);

        item.addEventListener("click", (event) => {
          event.stopPropagation();
        });

        equipmentMenu.appendChild(item);
      });
    }

    function getAllowedTraining(locationValue) {
      if (!locationValue) {
        return null;
      }
      const allowed = allowedTrainingMap[locationValue];
      return Array.isArray(allowed) ? allowed : null;
    }

    function refreshTrainingOptions() {
      const locationValue = locationSelect.value;
      const currentValue = trainingSelect.value;
      const allowed = getAllowedTraining(locationValue);

      if (!locationValue) {
        trainingSelect.disabled = true;
        setHintVisibility(trainingHintLocked, true);
        return;
      }

      trainingSelect.disabled = false;
      setHintVisibility(trainingHintLocked, false);

      const nextOptions = [];
      const seen = new Set();

      function pushOption(choice) {
        if (!choice || seen.has(choice.value)) {
          return;
        }
        seen.add(choice.value);
        nextOptions.push(choice);
      }

      if (allowed && allowed.length) {
        allowed.forEach((value) => pushOption(trainingChoiceMap.get(value)));
      } else {
        trainingChoices.forEach(pushOption);
      }

      if (currentValue && !seen.has(currentValue)) {
        const existing = trainingChoiceMap.get(currentValue);
        if (existing) {
          nextOptions.unshift(existing);
        }
      }

      trainingSelect.innerHTML = "";
      nextOptions.forEach((choice) => {
        const option = document.createElement("option");
        option.value = choice.value;
        option.textContent = choice.label;
        trainingSelect.appendChild(option);
      });

      let nextValue = currentValue;
      if (!nextOptions.length) {
        nextValue = "";
      } else if (!nextValue || !seen.has(nextValue)) {
        nextValue = nextOptions[0].value;
      }

      if (trainingSelect.value !== nextValue) {
        trainingSelect.value = nextValue;
        trainingSelect.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }

    function updateEquipmentControls({ preserveSelection = false } = {}) {
      const locationValue = locationSelect.value;
      const trainingValue = trainingSelect.value;

      const hasLocation = Boolean(locationValue);
      const hasTraining = Boolean(trainingValue);

      const shouldLockLocation = !hasLocation;
      const shouldLockTraining = hasLocation && !hasTraining;

      setHintVisibility(equipmentHintLocation, shouldLockLocation);
      setHintVisibility(equipmentHintTraining, shouldLockTraining);

      if (shouldLockLocation || shouldLockTraining) {
        equipmentToggle.disabled = true;
        currentEquipmentChoices = [];
        equipmentMenu.innerHTML = "";
        equipmentSelect.innerHTML = "";
        equipmentSelection = new Set();
        equipmentLabel.textContent = "Выберите экипировку";
        return;
      }

      equipmentToggle.disabled = false;

      const locationRules = allowedEquipmentMap[locationValue];
      const allowedList = locationRules ? locationRules[trainingValue] : null;

      let availableChoices;
      if (Array.isArray(allowedList)) {
        const allowedSet = new Set(allowedList);
        availableChoices = equipmentChoices.filter((choice) =>
          allowedSet.has(choice.value)
        );
        equipmentSelection = new Set(
          Array.from(equipmentSelection).filter((value) => allowedSet.has(value))
        );
        if (!preserveSelection && !equipmentModifiedByUser && !equipmentSelection.size) {
          const defaults = defaultsMap[trainingValue] || [];
          defaults.forEach((value) => {
            if (allowedSet.has(value)) {
              equipmentSelection.add(value);
            }
          });
        }
        if (!equipmentSelection.size && allowedList.length) {
          equipmentSelection.add(allowedList[0]);
        }
      } else {
        availableChoices = equipmentChoices.slice();
        if (!preserveSelection && !equipmentModifiedByUser && !equipmentSelection.size) {
          const defaults = defaultsMap[trainingValue] || [];
          defaults.forEach((value) => equipmentSelection.add(value));
        }
      }

      currentEquipmentChoices = availableChoices;
      renderEquipmentMenu(availableChoices);
      renderEquipmentSelect(availableChoices);
      updateEquipmentLabel();
    }

    function updateGroupRequirement() {
      if (!typeSelect || !groupSelect) {
        return;
      }
      const requiresGroup = typeSelect.value === "regular";
      groupSelect.required = requiresGroup;
      setHintVisibility(groupOptionalHint, !requiresGroup);
    }

    if (equipmentToggle) {
      equipmentToggle.addEventListener("click", (event) => {
        if (equipmentToggle.disabled) {
          event.preventDefault();
          event.stopPropagation();
        }
      });
    }

    if (locationSelect) {
      locationSelect.addEventListener("change", () => {
        equipmentModifiedByUser = false;
        refreshTrainingOptions();
        updateEquipmentControls();
      });
    }

    if (trainingSelect) {
      trainingSelect.addEventListener("change", () => {
        equipmentModifiedByUser = false;
        updateEquipmentControls();
      });
    }

    if (typeSelect) {
      typeSelect.addEventListener("change", updateGroupRequirement);
    }

    if (groupSelect && athleteCheckboxMap.size) {
      groupSelect.addEventListener("change", () => {
        const groupId = groupSelect.value;
        if (!groupId) {
          return;
        }
        const athleteIds = groupMap[groupId] || [];
        athleteIds.forEach((athleteId) => {
          const checkbox = athleteCheckboxMap.get(String(athleteId));
          if (checkbox && !checkbox.checked) {
            checkbox.checked = true;
          }
        });
      });
    }

    const presetButtons = form.querySelectorAll("[data-preset-button]");
    if (presetButtons.length) {
      presetButtons.forEach((button) => {
        button.addEventListener("click", () => {
          const { presetType, presetLocation, presetTraining, presetEquipment } =
            button.dataset;

          if (presetType && typeSelect) {
            typeSelect.value = presetType;
            typeSelect.dispatchEvent(new Event("change", { bubbles: true }));
          }
          if (presetLocation && locationSelect) {
            locationSelect.value = presetLocation;
            locationSelect.dispatchEvent(new Event("change", { bubbles: true }));
          }
          if (presetTraining && trainingSelect) {
            const hasPresetOption = Array.from(trainingSelect.options).some(
              (option) => option.value === presetTraining
            );
            if (!hasPresetOption) {
              const presetChoice = trainingChoiceMap.get(presetTraining);
              if (presetChoice) {
                const option = document.createElement("option");
                option.value = presetChoice.value;
                option.textContent = presetChoice.label;
                trainingSelect.appendChild(option);
              }
            }
            trainingSelect.value = presetTraining;
            trainingSelect.dispatchEvent(new Event("change", { bubbles: true }));
          } else {
            updateEquipmentControls();
          }

          const equipmentValues = (presetEquipment || "")
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean);
          equipmentSelection = new Set(equipmentValues);
          equipmentModifiedByUser = true;
          updateEquipmentControls({ preserveSelection: true });
          updateEquipmentLabel();
        });
      });
    }

    refreshTrainingOptions();
    updateEquipmentControls();
    updateEquipmentLabel();
    updateGroupRequirement();
  });
})();
