// Small progressive enhancements. The site works without JavaScript.
document.addEventListener("DOMContentLoaded", function () {
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  // Loading state: disable the submit button after the first click to prevent double submits.
  document.querySelectorAll("form[data-loading]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector("button[type=submit]");
      if (btn && !btn.disabled) {
        btn.setAttribute("data-label", btn.textContent);
        btn.textContent = form.getAttribute("data-loading") || "Please wait…";
        setTimeout(function () { btn.disabled = true; }, 0);
      }
    });
  });
});
