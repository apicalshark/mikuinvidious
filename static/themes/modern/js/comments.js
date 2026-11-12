/**
 * Shared comment loading, sub-comment expand/fold, and VOD component bootstrap
 * for video.html and video_listen.html.
 */
(function () {
  "use strict";

  function updateDownloadDropdown() {
    const select = document.getElementById("download-quality");
    const btn = document.getElementById("download-btn");
    if (!select || !window.supported_src || window.supported_src.length === 0) return;

    select.innerHTML = "";
    window.supported_src.forEach((src) => {
      const opt = document.createElement("option");
      opt.value = src.quality;
      opt.textContent = src.new_description;
      select.appendChild(opt);
    });

    if (btn) btn.disabled = false;
  }

  window.updateDownloadDropdown = updateDownloadDropdown;

  document.addEventListener("DOMContentLoaded", function () {
    const MSG_FOLD = I18n.t("Fold");
    const MSG_MORE_REPLIES = I18n.t("Load more replies");
    const MSG_LOADING_DOTS = I18n.t("Loading...");
    const MSG_LOAD_FAILED = I18n.t("Load failed");
    const MSG_LOAD_MORE_COMMENTS = I18n.t("Load more comments");

    function repliesLabel(btn) {
      const c = Number(btn.dataset.count);
      if (typeof I18n.n === "function") {
        return I18n.n("%(count)s reply", "%(count)s replies", c, { count: c });
      }
      return c === 1
        ? I18n.t("%(count)s reply", { count: c })
        : I18n.t("%(count)s replies", { count: c });
    }

    function paintSubToggle(btn, slot, expanded) {
      btn.dataset.expanded = expanded ? "1" : "0";
      const label = btn.querySelector("span");
      if (!label) return;
      if (expanded) {
        if (!slot.dataset.foldLabel) slot.dataset.foldLabel = label.textContent;
        label.textContent = MSG_FOLD;
      } else if (slot.dataset.foldLabel) {
        label.textContent = slot.dataset.foldLabel;
      }
    }

    const dlForm = document.getElementById("download-form");
    if (dlForm && window.MikuDownload) {
      window.MikuDownload.attach(dlForm);
    }

    if (window.is_live) return;

    const cspNonce =
      document.querySelector('meta[name="csp-nonce"]')?.getAttribute("content") || "";
    const headers = cspNonce ? { "X-CSP-Nonce": cspNonce } : {};

    const playerWrapper = document.getElementById("player-placeholder-wrapper");
    if (playerWrapper && window.current_vid) {
      const ep = encodeURIComponent(window.ep_id || "");
      fetch(`/api/component/player/${window.current_vid}/${window.idx}?ep_id=${ep}`, { headers })
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.text();
        })
        .then((html) => {
          playerWrapper.innerHTML = "";
          const range = document.createRange();
          playerWrapper.appendChild(range.createContextualFragment(html));
          setTimeout(updateDownloadDropdown, 100);
        })
        .catch((err) => {
          console.error("[player] refresh failed:", err);
        });
    }

    const commentsContainer = document.getElementById("comments-loader-container");
    if (commentsContainer && window.current_vid) {
      fetch(`/api/component/meta/${window.current_vid}/${window.idx}`, { headers })
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.text();
        })
        .then((html) => {
          commentsContainer.innerHTML = html;
        })
        .catch((err) => {
          console.error("[comments] fetch failed:", err);
          const commentsTitle = I18n.t("Comments");
          const commentsFailed = I18n.t("Failed to load comments, please try again later");
          commentsContainer.innerHTML =
            '<div class="bg-surface dark:bg-background rounded-2xl p-4 md:p-6 shadow-xs">' +
            `<h3 class="text-lg font-bold mb-4 text-on-surface flex items-center gap-2"><i class="icon ion-ios-chatbubbles"></i> ${commentsTitle}</h3>` +
            `<p class="text-center text-on-surface-variant py-8">${commentsFailed}</p>` +
            "</div>";
        });
    }

    function fetchSubPage(vid, root, page) {
      return fetch(`/api/component/comments/${vid}/${root}?page=${encodeURIComponent(page)}`, {
        headers,
      })
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.text();
        })
        .then((html) => {
          const tmp = document.createElement("div");
          tmp.innerHTML = html;
          const pageEl = tmp.querySelector(".sub-comments-page");
          if (!pageEl) throw new Error("Invalid sub-comment response");
          return pageEl;
        });
    }

    function appendSubPage(slot, pageEl) {
      const existing = new Set(
        Array.from(slot.querySelectorAll("[data-rpid]"), (el) => el.dataset.rpid)
      );
      pageEl.querySelectorAll("[data-rpid]").forEach((el) => {
        if (existing.has(el.dataset.rpid)) el.remove();
        else existing.add(el.dataset.rpid);
      });
      const more = slot.querySelector(".sub-more-btn");
      slot.insertBefore(pageEl, more || null);
      return {
        isEnd: pageEl.dataset.isEnd === "true",
        page: Number(pageEl.dataset.page),
      };
    }

    function ensureSubMoreBtn(slot, btn) {
      if (slot.dataset.loaded === "1" || slot.querySelector(".sub-more-btn")) return;
      const more = document.createElement("button");
      more.type = "button";
      more.className =
        "sub-more-btn mt-2 flex items-center gap-1 text-xs text-on-surface-variant/70 hover:text-primary transition-colors cursor-pointer";
      more.dataset.next = "2";
      const label = document.createElement("span");
      label.textContent = MSG_MORE_REPLIES;
      more.appendChild(label);
      more.addEventListener("click", () => {
        if (more.dataset.loading === "1") return;
        more.dataset.loading = "1";
        label.textContent = MSG_LOADING_DOTS;
        fetchSubPage(btn.dataset.vid, btn.dataset.root, more.dataset.next)
          .then((pageEl) => {
            const st = appendSubPage(slot, pageEl);
            more.dataset.next = String(st.page + 1);
            if (st.isEnd) {
              slot.dataset.loaded = "1";
              more.remove();
              slot.dataset.foldLabel = repliesLabel(btn);
            } else {
              label.textContent = MSG_MORE_REPLIES;
            }
          })
          .catch(() => {
            label.textContent = MSG_LOAD_FAILED;
          })
          .finally(() => {
            delete more.dataset.loading;
          });
      });
      slot.appendChild(more);
    }

    let commentsLoading = false;
    window.loadMoreComments = async function (vid, idx) {
      if (commentsLoading) return;
      const cursorEl = document.getElementById("comments-cursor");
      const moreContainer = document.getElementById("comments-load-more-container");
      const moreBtn = document.getElementById("comments-load-more-btn");
      const label = moreBtn ? moreBtn.querySelector("span") : null;
      if (!cursorEl) return;
      const next = cursorEl.dataset.nextOffset;
      if (!next || cursorEl.dataset.isEnd === "true") {
        if (moreContainer) moreContainer.classList.add("hidden");
        return;
      }

      const previousNext = next;
      commentsLoading = true;
      if (moreBtn) moreBtn.disabled = true;
      if (label) label.textContent = MSG_LOADING_DOTS;

      try {
        const r = await fetch(
          `/api/component/comments/${vid}/${idx}/more?next=${encodeURIComponent(next)}`,
          { headers }
        );
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const html = await r.text();
        const list = document.getElementById("comments-list");
        if (list && cursorEl) {
          const tmp = document.createElement("div");
          tmp.innerHTML = html;
          const newCursor = tmp.querySelector("#comments-cursor");
          const items = tmp.querySelectorAll("[data-rpid]");
          const existing = new Set(
            Array.from(list.querySelectorAll("[data-rpid]"), (el) => el.dataset.rpid)
          );
          items.forEach((el) => {
            if (!existing.has(el.dataset.rpid)) {
              existing.add(el.dataset.rpid);
              list.appendChild(el);
            }
          });
          if (newCursor) {
            cursorEl.dataset.nextOffset = newCursor.dataset.nextOffset;
            cursorEl.dataset.isEnd = newCursor.dataset.isEnd;
          }
        }
        const cursorRepeated = cursorEl && cursorEl.dataset.nextOffset === previousNext;
        const isEnd =
          !cursorEl ||
          cursorEl.dataset.isEnd === "true" ||
          !cursorEl.dataset.nextOffset ||
          cursorRepeated;
        if (isEnd) {
          if (moreContainer) moreContainer.classList.add("hidden");
        } else {
          if (label) label.textContent = MSG_LOAD_MORE_COMMENTS;
          if (moreBtn) moreBtn.disabled = false;
        }
      } catch (err) {
        console.error("[comments] load more failed:", err);
        if (label) label.textContent = MSG_LOAD_FAILED;
        if (moreBtn) moreBtn.disabled = false;
      } finally {
        commentsLoading = false;
      }
    };

    document.addEventListener("click", function (e) {
      const moreBtn = e.target.closest("#comments-load-more-btn");
      if (moreBtn) {
        const vid = moreBtn.dataset.vid || window.current_vid;
        const idx = moreBtn.dataset.idx !== undefined ? moreBtn.dataset.idx : window.idx || 0;
        window.loadMoreComments(vid, idx);
        return;
      }

      const btn = e.target.closest(".sub-comment-toggle");
      if (!btn) return;
      const vid = btn.dataset.vid;
      const root = btn.dataset.root;
      const slot = btn.closest(".grow")?.querySelector(".sub-comment-slot");
      if (!slot || slot.dataset.loading === "1") return;
      if (slot.dataset.page1) {
        slot.classList.toggle("hidden");
        paintSubToggle(btn, slot, !slot.classList.contains("hidden"));
        return;
      }
      const span = btn.querySelector("span");
      if (span) span.textContent = MSG_LOADING_DOTS;
      slot.dataset.loading = "1";
      fetchSubPage(vid, root, "1")
        .then((pageEl) => {
          const st = appendSubPage(slot, pageEl);
          slot.dataset.page1 = "1";
          if (st.isEnd) slot.dataset.loaded = "1";
          else ensureSubMoreBtn(slot, btn);
          slot.dataset.foldLabel = repliesLabel(btn);
          paintSubToggle(btn, slot, true);
        })
        .catch(() => {
          if (span) span.textContent = MSG_LOAD_FAILED;
        })
        .finally(() => {
          delete slot.dataset.loading;
        });
    });
  });
})();
