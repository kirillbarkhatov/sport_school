(function () {
  const formatDate = (date) => {
    if (!(date instanceof Date)) return '';
    const tzOffset = date.getTimezoneOffset() * 60000;
    const localISO = new Date(date.getTime() - tzOffset).toISOString().slice(0, 10);
    return localISO;
  };

  const setInputValue = (input, value) => {
    if (!input) return;
    input.value = value;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  };

  document.addEventListener('DOMContentLoaded', () => {
    const inputs = document.querySelectorAll('[data-contract-date="true"]');
    inputs.forEach((input) => {
      const group = input.closest('.input-group');
      if (!group) return;

      const defaultValue = input.dataset.defaultDate || '';
      const todayButton = group.querySelector('[data-set-date="today"]');
      const defaultButton = group.querySelector('[data-set-date="default"]');

      if (todayButton) {
        todayButton.addEventListener('click', () => {
          const todayISO = formatDate(new Date());
          setInputValue(input, todayISO);
        });
      }

      if (defaultButton) {
        defaultButton.addEventListener('click', () => {
          if (defaultValue) {
            setInputValue(input, defaultValue);
          }
        });
      }
    });
  });
})();
