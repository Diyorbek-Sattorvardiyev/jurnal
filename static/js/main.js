const toggleButton = document.querySelector("[data-toggle-sidebar]");
const sidebar = document.getElementById("sidebar");
const themeToggle = document.querySelector("[data-theme-toggle]");

if (toggleButton && sidebar) {
  toggleButton.addEventListener("click", () => {
    sidebar.classList.toggle("open");
  });
}

const applyTheme = (theme) => {
  document.documentElement.dataset.theme = theme;
  if (themeToggle) {
    themeToggle.textContent = theme === "dark" ? "Yorug' rejim" : "Tungi rejim";
  }
};

const savedTheme = window.localStorage.getItem("ej-theme") || "light";
applyTheme(savedTheme);

if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    window.localStorage.setItem("ej-theme", nextTheme);
    applyTheme(nextTheme);
  });
}

document.querySelectorAll("[data-confirm]").forEach((button) => {
  button.addEventListener("click", (event) => {
    const message = button.getAttribute("data-confirm");
    if (!window.confirm(message)) {
      event.preventDefault();
    }
  });
});

document.querySelectorAll("[data-image-input]").forEach((imageInput) => {
  const container = imageInput.closest("form") || document;
  const imagePreview = container.querySelector("[data-image-preview]");
  if (!imagePreview) return;

  imageInput.addEventListener("change", () => {
    const [file] = imageInput.files;
    if (!file) return;
    imagePreview.src = URL.createObjectURL(file);
  });
});

const roleSelect = document.querySelector("[data-role-select]");
const userForm = document.querySelector("[data-user-form]");

if (roleSelect && userForm) {
  const toggleGroupField = () => {
    const groupField = userForm.querySelector(".group-only");
    if (!groupField) return;
    groupField.classList.toggle("hidden", roleSelect.value !== "student");
  };
  roleSelect.addEventListener("change", toggleGroupField);
  toggleGroupField();
}

document.querySelectorAll(".mode-switch input[type='radio']").forEach((radio) => {
  radio.addEventListener("change", () => {
    const form = radio.closest("form");
    if (form) form.submit();
  });
});
