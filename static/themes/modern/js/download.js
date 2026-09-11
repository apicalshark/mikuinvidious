/* Floating download dialog for MikuInvidious.
 *
 * Intercepts the video / listen download forms: instead of opening a blank
 * tab that hangs until the server finishes fetching + muxing, it opens a
 * floating dialog with live progress ("downloading %", "muxing", server
 * speed), a progress bar, and a cancel button.
 *
 * Server protocol (see python/dash_proxy.py background download jobs):
 *   POST /download            (XHR branch) -> {"job_id": ...}
 *   GET  /download/status/<id>             -> {state, percent, done_bytes,
 *                                              total_bytes, speed_bps,
 *                                              filename, error}
 *   GET  /download/file/<id>               -> finished MP4 attachment
 *   POST /download/cancel/<id>             -> abort server-side work
 *
 * Job IDs are per-download (uuid), so many users can download concurrently;
 * cancelling only affects the caller's own job. Cancelling (button, dialog
 * close, or page close) aborts the server-side fetch/mux and deletes temp
 * files. Usage: MikuDownload.attach(formElement).
 */
(function () {
    "use strict";

    var POLL_MS = 1000;
    var MAX_POLL_FAILURES = 8;

    var dlg = null;      // lazily-built dialog element refs
    var active = null;   // {jobId, csrf, timer, failures, terminal, fileUrl, form}

    function fmtBytes(n) {
        if (!n || n <= 0) return "0 B";
        var units = ["B", "KB", "MB", "GB"];
        var i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
        return (n / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + " " + units[i];
    }

    function fmtSpeed(bps) {
        if (!bps || bps <= 0) return "";
        return fmtBytes(bps) + "/s";
    }

    function buildDialog() {
        var backdrop = document.createElement("div");
        backdrop.id = "miku-dl-backdrop";
        backdrop.className = "fixed inset-0 z-[100] hidden items-center justify-center bg-black/60 p-4";
        backdrop.innerHTML =
            '<div class="w-full max-w-md rounded-2xl bg-surface text-on-surface p-6 shadow-2xl" role="dialog" aria-modal="true" aria-label="下载进度" tabindex="-1">' +
            '<div class="flex items-start justify-between gap-4">' +
            '<h3 class="text-lg font-bold flex items-center gap-2"><i class="icon ion-md-download"></i>下载视频</h3>' +
            '<button id="miku-dl-close" class="text-on-surface-variant hover:text-on-surface text-xl leading-none px-1" aria-label="关闭">&times;</button>' +
            "</div>" +
            '<p id="miku-dl-file" class="mt-1 text-sm text-on-surface-variant truncate"></p>' +
            '<div class="mt-4 h-2.5 rounded-full bg-surface-variant/40 overflow-hidden">' +
            '<div id="miku-dl-bar" class="h-full w-0 rounded-full bg-primary transition-[width] duration-300"></div>' +
            "</div>" +
            '<p id="miku-dl-status" class="mt-3 text-sm text-on-surface-variant">准备中…</p>' +
            '<a id="miku-dl-retry" class="mt-1 hidden text-sm text-primary underline" href="#">如果下载没有自动开始，点击这里重试</a>' +
            '<div class="mt-5 flex justify-end gap-2">' +
            '<button id="miku-dl-cancel" class="md3-button md3-button-tonal">取消下载</button>' +
            '<button id="miku-dl-ok" class="md3-button md3-button-filled hidden">关闭</button>' +
            "</div></div>";
        document.body.appendChild(backdrop);
        var refs = {
            backdrop: backdrop,
            dialog: backdrop.querySelector('[role="dialog"]'),
            file: backdrop.querySelector("#miku-dl-file"),
            bar: backdrop.querySelector("#miku-dl-bar"),
            status: backdrop.querySelector("#miku-dl-status"),
            retry: backdrop.querySelector("#miku-dl-retry"),
            cancel: backdrop.querySelector("#miku-dl-cancel"),
            ok: backdrop.querySelector("#miku-dl-ok"),
            close: backdrop.querySelector("#miku-dl-close"),
            previousFocus: null,
        };
        refs.close.addEventListener("click", onCloseButton);
        refs.ok.addEventListener("click", hideDialog);
        refs.cancel.addEventListener("click", onCancelButton);
        refs.backdrop.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                event.preventDefault();
                onCloseButton();
                return;
            }
            if (event.key !== "Tab") return;

            var controls = Array.prototype.filter.call(
                refs.dialog.querySelectorAll(
                    'a[href], button:not([disabled]), input:not([disabled]), ' +
                    'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'),
                function (control) { return !control.closest(".hidden"); });
            if (!controls.length) {
                event.preventDefault();
                refs.dialog.focus();
                return;
            }
            var first = controls[0];
            var last = controls[controls.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        });
        return refs;
    }

    function ensureDialog() {
        if (!dlg) dlg = buildDialog();
        return dlg;
    }

    function showDialog() {
        var d = ensureDialog();
        if (d.backdrop.classList.contains("hidden")) {
            d.previousFocus = document.activeElement;
        }
        d.backdrop.classList.remove("hidden");
        d.backdrop.classList.add("flex");
        d.close.focus();
    }

    function hideDialog() {
        if (!dlg) return;
        var previousFocus = dlg.previousFocus;
        dlg.previousFocus = null;
        dlg.backdrop.classList.add("hidden");
        dlg.backdrop.classList.remove("flex");
        if (previousFocus && previousFocus.isConnected && previousFocus.focus) {
            previousFocus.focus();
        }
    }

    function setBar(percent) {
        var d = ensureDialog();
        if (percent === null || percent === undefined) {
            // Indeterminate: full-width pulsing bar (resolving / muxing phases).
            d.bar.style.width = "100%";
            d.bar.classList.add("animate-pulse");
        } else {
            d.bar.classList.remove("animate-pulse");
            d.bar.style.width = Math.max(0, Math.min(100, percent)) + "%";
        }
    }

    function render(st) {
        var d = ensureDialog();
        var label = "";
        if (st.state === "queued") {
            label = "排队中…（服务器同时下载数有限）";
            setBar(null);
        } else if (st.state === "resolving") {
            label = "解析视频信息…";
            setBar(null);
        } else if (st.state === "downloading") {
            var speed = fmtSpeed(st.speed_bps);
            var bytes = fmtBytes(st.done_bytes) +
                (st.total_bytes > 0 ? " / " + fmtBytes(st.total_bytes) : "");
            if (st.note) {
                // Transient server note, e.g. retry backoff after an upstream cut.
                label = st.note + " " + bytes;
                setBar(st.percent !== null && st.percent !== undefined ? st.percent : null);
            } else if (st.percent !== null && st.percent !== undefined) {
                label = "下载中 " + st.percent + "% · " + bytes + (speed ? " · " + speed : "");
                setBar(st.percent);
            } else {
                label = "下载中… " + bytes + (speed ? " · " + speed : "");
                setBar(null);
            }
        } else if (st.state === "muxing") {
            label = "混流中…（合并音视频，请稍候）";
            setBar(null);
        } else if (st.state === "ready") {
            label = "完成，浏览器已开始下载";
            setBar(100);
        } else if (st.state === "cancelled") {
            label = "已取消";
            setBar(0);
        } else if (st.state === "error") {
            label = "下载失败" + (st.error ? "：" + st.error : "");
            setBar(0);
        }
        d.status.textContent = label;
        var terminal = st.state === "ready" || st.state === "error" || st.state === "cancelled";
        d.cancel.classList.toggle("hidden", terminal);
        d.ok.classList.toggle("hidden", !terminal);
        if (st.state === "ready" && active) {
            d.retry.href = active.fileUrl;
            d.retry.classList.remove("hidden");
        } else {
            d.retry.classList.add("hidden");
        }
    }

    function stopPolling() {
        if (active && active.timer) {
            clearTimeout(active.timer);
            active.timer = null;
        }
    }

    async function pollStatus() {
        if (!active || active.terminal) return;
        var current = active;
        var jobId = current.jobId;
        try {
            var resp = await fetch("/download/status/" + encodeURIComponent(jobId), {
                credentials: "same-origin",
            });
            if (active !== current || active.jobId !== jobId) return;
            if (resp.status === 404) {
                current.terminal = true;
                render({ state: "error", error: "任务已过期，请重新下载" });
                return;
            }
            if (!resp.ok) throw new Error("HTTP " + resp.status);
            var st = await resp.json();
            if (active !== current || active.jobId !== jobId) return;
            current.failures = 0;
            render(st);
            if (st.state === "ready" || st.state === "error" || st.state === "cancelled") {
                current.terminal = true;
                if (st.state === "ready") triggerBrowserDownload();
                return;
            }
        } catch (err) {
            if (active !== current || active.jobId !== jobId) return;
            current.failures += 1;
            if (current.failures >= MAX_POLL_FAILURES) {
                current.terminal = true;
                render({ state: "error", error: "与服务器失去连接，请重试" });
                return;
            }
        }
        if (active !== current || active.jobId !== jobId) return;
        current.timer = setTimeout(pollStatus, POLL_MS);
    }

    function triggerBrowserDownload() {
        if (!active) return;
        var a = document.createElement("a");
        a.href = active.fileUrl;
        a.setAttribute("download", "");
        a.rel = "noopener";
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { a.remove(); }, 1000);
    }

    async function cancelActive(silent) {
        if (!active || active.terminal) return;
        var current = active;
        var jobId = current.jobId;
        var csrf = current.csrf;
        stopPolling();
        current.terminal = true;
        try {
            var body = new FormData();
            body.append("csrf_token", csrf);
            await fetch("/download/cancel/" + encodeURIComponent(jobId), {
                method: "POST",
                body: body,
                credentials: "same-origin",
            });
        } catch (err) {
            /* best effort: page may be unloading */
        }
        if (active !== current || active.jobId !== jobId) return;
        if (!silent) render({ state: "cancelled" });
    }

    function onCancelButton() {
        cancelActive(false);
    }

    function onCloseButton() {
        // Closing mid-download cancels the server-side work (temp files freed).
        if (active && !active.terminal) {
            cancelActive(false);
        }
        hideDialog();
    }

    function onPageHide() {
        // Best-effort server-side cancel when the tab is closed/navigated away.
        if (active && !active.terminal) {
            var body = new FormData();
            body.append("csrf_token", active.csrf);
            try {
                navigator.sendBeacon(
                    "/download/cancel/" + encodeURIComponent(active.jobId), body);
            } catch (err) { /* ignore */ }
        }
    }

    async function startFromForm(form) {
        // One dialog per page: a new download supersedes the previous one.
        if (active && !active.terminal) await cancelActive(true);

        var d = ensureDialog();
        var data = new FormData(form);
        var csrf = data.get("csrf_token") || "";
        var sel = form.querySelector('select[name="qual"]');
        var qualText = sel && sel.selectedIndex >= 0
            ? sel.options[sel.selectedIndex].text : "";

        showDialog();
        d.file.textContent = qualText || "准备下载…";
        d.retry.classList.add("hidden");
        d.cancel.classList.remove("hidden");
        d.ok.classList.add("hidden");
        d.status.textContent = "正在创建下载任务…";
        setBar(null);

        var jobId;
        try {
            var resp = await fetch(form.action, {
                method: "POST",
                body: data,
                credentials: "same-origin",
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json",
                },
            });
            var payload = await resp.json().catch(function () { return {}; });
            if (!resp.ok || !payload.job_id) {
                throw new Error(payload.error || ("HTTP " + resp.status));
            }
            jobId = payload.job_id;
        } catch (err) {
            render({ state: "error", error: String((err && err.message) || err) });
            active = { terminal: true };
            return;
        }

        active = {
            jobId: jobId,
            csrf: csrf,
            timer: null,
            failures: 0,
            terminal: false,
            fileUrl: "/download/file/" + encodeURIComponent(jobId),
            form: form,
        };
        pollStatus();
    }

    window.MikuDownload = {
        attach: function (form) {
            if (typeof form === "string") form = document.getElementById(form);
            if (!form || form.__mikuDlAttached) return;
            form.__mikuDlAttached = true;
            form.removeAttribute("target"); // dialog replaces the blank tab
            form.addEventListener("submit", function (ev) {
                ev.preventDefault();
                startFromForm(form);
            });
            window.addEventListener("pagehide", onPageHide);
        },
    };
})();
