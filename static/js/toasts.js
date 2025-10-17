(() => {
  const ensureContainer = () =>
    document.getElementById("toast-container") ||
    document.body.appendChild(
      Object.assign(document.createElement("div"), {
        id: "toast-container",
        className: "toast-container position-fixed top-0 end-0 p-3",
        style: "z-index: 1100;",
      })
    );

  const createToastElement = (message, variant) => {
    const toast = document.createElement("div");
    toast.className = `toast align-items-center text-bg-${variant} border-0`;
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    toast.setAttribute("aria-atomic", "true");
    toast.setAttribute("data-bs-delay", "4000");

    toast.innerHTML = `
      <div class="d-flex">
        <div class="toast-body">${message}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Закрыть"></button>
      </div>
    `;
    return toast;
  };

  window.showToast = (message, tone = "success") => {
    const container = ensureContainer();
    const variant = ["success", "danger", "warning", "info", "primary"].includes(
      tone
    )
      ? tone
      : "primary";

    const toastEl = createToastElement(message, variant);
    container.appendChild(toastEl);

    if (typeof bootstrap !== "undefined" && bootstrap.Toast) {
      const toast = new bootstrap.Toast(toastEl);
      toast.show();
      toastEl.addEventListener("hidden.bs.toast", () => toastEl.remove(), {
        once: true,
      });
    } else {
      toastEl.classList.add("show");
    }
  };
})();
