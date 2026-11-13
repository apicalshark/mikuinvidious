(function () {
  var form = document.getElementById("convert_form");
  var input = document.getElementById("convert_input");
  var btn = document.getElementById("convert_btn");
  var btnText = document.getElementById("btn_text");
  var btnLoading = document.getElementById("btn_loading");
  var errBox = document.getElementById("error_container");
  var errText = document.getElementById("error_text");
  var resultBox = document.getElementById("result_container");
  var typeBadge = document.getElementById("type_badge");
  var cleanUrlInput = document.getElementById("clean_url_input");
  var localUrlInput = document.getElementById("local_url_input");
  var copyCleanBtn = document.getElementById("copy_clean_btn");
  var copyLocalBtn = document.getElementById("copy_local_btn");
  var openLocalBtn = document.getElementById("open_local_btn");

  if (localUrlInput && localUrlInput.value && localUrlInput.value.startsWith("/")) {
    localUrlInput.value = window.location.origin + localUrlInput.value;
  }

  function fallbackCopy(text, cb) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      document.execCommand("copy");
      if (cb) cb();
    } catch (_e) {
      // Clipboard fallback may fail in restricted contexts.
    }
    document.body.removeChild(ta);
  }

  function copyToClipboard(text, btnElement) {
    if (!text) return;
    var label = btnElement.querySelector(".btn-label");
    var originalText = label ? label.textContent : "";

    function setCopied() {
      if (label) label.textContent = I18n.t("Copied!");
      btnElement.classList.add("is-on");
      setTimeout(function () {
        if (label) label.textContent = originalText;
        btnElement.classList.remove("is-on");
      }, 2000);
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard
        .writeText(text)
        .then(setCopied)
        .catch(function () {
          fallbackCopy(text, setCopied);
        });
    } else {
      fallbackCopy(text, setCopied);
    }
  }

  if (copyCleanBtn) {
    copyCleanBtn.addEventListener("click", function () {
      copyToClipboard(cleanUrlInput.value, copyCleanBtn);
    });
  }

  if (copyLocalBtn) {
    copyLocalBtn.addEventListener("click", function () {
      copyToClipboard(localUrlInput.value, copyLocalBtn);
    });
  }

  if (form) {
    form.addEventListener("submit", function (e) {
      var val = (input.value || "").trim();
      if (!val) return;
      e.preventDefault();

      btnText.classList.add("hidden");
      btnLoading.classList.remove("hidden");
      btnLoading.classList.add("flex");
      btn.disabled = true;

      var metaEl = document.querySelector('meta[name="csrf-token"]');
      var formTokenEl = form.querySelector('input[name="csrf_token"]');
      var csrfToken =
        (metaEl && metaEl.getAttribute("content")) || (formTokenEl && formTokenEl.value) || "";

      fetch("/api/convert", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ url: val }),
      })
        .then(function (res) {
          return res
            .json()
            .catch(function () {
              return {};
            })
            .then(function (data) {
              return { ok: res.ok, data: data };
            });
        })
        .then(function (resObj) {
          btnText.classList.remove("hidden");
          btnLoading.classList.add("hidden");
          btnLoading.classList.remove("flex");
          btn.disabled = false;

          if (!resObj.ok || resObj.data.status !== "ok") {
            var msg =
              resObj.data && resObj.data.error
                ? resObj.data.error
                : I18n.t("Failed to resolve URL");
            errText.textContent = msg;
            errBox.classList.remove("hidden");
            resultBox.classList.add("hidden");
            return;
          }

          var data = resObj.data;
          errBox.classList.add("hidden");
          typeBadge.textContent = (data.type || "").toUpperCase();
          cleanUrlInput.value = data.clean_url || "";
          localUrlInput.value = data.local_path ? window.location.origin + data.local_path : "";
          openLocalBtn.href = data.local_path || "#";

          resultBox.classList.remove("hidden");
        })
        .catch(function () {
          btnText.classList.remove("hidden");
          btnLoading.classList.add("hidden");
          btnLoading.classList.remove("flex");
          btn.disabled = false;

          errText.textContent = I18n.t("Network error. Please try again.");
          errBox.classList.remove("hidden");
          resultBox.classList.add("hidden");
        });
    });
  }
})();
