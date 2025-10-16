(function () {
  const computeSeason = () => {
    const today = new Date();
    const currentYear = today.getFullYear();
    const seasonBoundary = new Date(currentYear, 8, 1); // September 1st
    const seasonStart = today < seasonBoundary ? currentYear - 1 : currentYear;
    return `${seasonStart}-${seasonStart + 1}`;
  };

  const initForm = (form) => {
    const profileSelect = form.querySelector('[data-profile-select]');
    const typeSelect = form.querySelector('[data-service-type]');
    const nameInput = form.querySelector('[data-service-name]');

    if (!nameInput) {
      return;
    }

    const lockName = form.dataset.lockName === 'true';
    if (lockName) {
      nameInput.readOnly = true;
      nameInput.dataset.userEdited = 'false';
    } else {
      if (!nameInput.dataset.userEdited) {
        nameInput.dataset.userEdited = 'true';
      }
    }

    const updateName = () => {
      if (!profileSelect || !typeSelect) {
        return;
      }
      const selectedProfile = profileSelect.options[profileSelect.selectedIndex];
      const selectedType = typeSelect.options[typeSelect.selectedIndex];
      if (!selectedProfile || !selectedProfile.value || !selectedType || !selectedType.value) {
        return;
      }
      const athleteName = selectedProfile.text.trim();
      const serviceLabel = selectedType.text.trim();
      const season = computeSeason();
      const generatedName = `${serviceLabel} - ${athleteName} - сезон ${season}`;
      if (!nameInput.dataset.userEdited || nameInput.dataset.userEdited === 'false') {
        nameInput.value = generatedName;
      }
      nameInput.readOnly = false;
    };

    if (profileSelect) {
      profileSelect.addEventListener('change', () => {
        if (nameInput) {
          nameInput.dataset.userEdited = 'false';
        }
        updateName();
      });
    }

    if (typeSelect) {
      typeSelect.addEventListener('change', () => {
        if (nameInput) {
          nameInput.dataset.userEdited = 'false';
        }
        updateName();
      });
    }

    nameInput.addEventListener('input', () => {
      nameInput.dataset.userEdited = 'true';
    });
  };

  document.addEventListener('DOMContentLoaded', () => {
    const forms = document.querySelectorAll('[data-family-service-form]');
    forms.forEach(initForm);
  });
})();
