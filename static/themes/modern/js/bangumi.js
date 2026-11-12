(function () {
  function initBangumiIndex() {
    document.querySelectorAll(".bangumi-filter").forEach(function (el) {
      el.addEventListener("change", function () {
        var url = new URL(window.location.href);
        url.searchParams.set(el.dataset.key, el.value);
        if (url.searchParams.has("page")) url.searchParams.delete("page");
        window.location.href = url.toString();
      });
    });
    document.querySelectorAll(".bangumi-page").forEach(function (el) {
      el.addEventListener("click", function () {
        var url = new URL(window.location.href);
        url.searchParams.set(el.dataset.key, el.dataset.value);
        window.location.href = url.toString();
      });
    });
  }

  function initBangumiNyaa() {
    var sidebarContainer = document.getElementById("nyaa-sidebar-container");
    if (!sidebarContainer || !sidebarContainer.dataset.nyaaSsid) return;

    var ssid = sidebarContainer.dataset.nyaaSsid;
    var msgNyaaMagnet = I18n.t("Nyaa Magnet Resources");

    fetch("/bangumi/api/nyaa/" + encodeURIComponent(ssid))
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        var epTorrents = data.ep_torrents || {};
        if (!data.sidebar_html && !Object.keys(epTorrents).length) {
          var empty = document.createElement("div");
          empty.className = "text-center py-12 opacity-40 italic";
          empty.textContent = I18n.t("Failed to load Nyaa resources");
          sidebarContainer.replaceChildren(empty);
          return;
        }
        sidebarContainer.innerHTML = data.sidebar_html;
        Object.keys(epTorrents).forEach(function (epNum) {
          var iconPlaceholder = document.getElementById("ep-nyaa-" + epNum);
          var contentPlaceholder = document.querySelector(
            "#ep-torrents-" + epNum + " .ep-torrents-content"
          );
          var torrents = epTorrents[epNum];
          if (!iconPlaceholder || !contentPlaceholder || !torrents.length) return;

          iconPlaceholder.classList.remove("hidden");
          var badge = document.createElement("span");
          badge.className =
            "text-[10px] bg-primary/10 text-primary px-1.5 py-0.5 rounded font-mono";
          badge.textContent = String(torrents.length);
          iconPlaceholder.replaceChildren(badge);

          var wrapper = document.createElement("div");
          wrapper.className = "pt-3";

          var label = document.createElement("p");
          label.className =
            "text-[9px] font-bold text-primary/60 uppercase tracking-widest mb-2 px-1";
          label.textContent = msgNyaaMagnet;
          wrapper.appendChild(label);

          var grid = document.createElement("div");
          grid.className = "grid grid-cols-1 gap-1.5";

          torrents.forEach(function (t) {
            var a = document.createElement("a");
            a.className =
              "w-full p-2 bg-black/5 dark:bg-white/5 hover:bg-primary/10 text-on-surface rounded-lg text-[11px] transition-all flex items-center justify-between group/torrent text-left";
            var magnet =
              typeof t.magnet === "string" && t.magnet.startsWith("magnet:?") ? t.magnet : "#";
            a.setAttribute("href", magnet);
            a.setAttribute("title", String(t.title || ""));

            var titleSpan = document.createElement("span");
            titleSpan.className = "truncate pr-4 grow opacity-80 group-hover/torrent:opacity-100";

            var magnetIcon = document.createElement("i");
            magnetIcon.className = "icon ion-md-magnet text-primary mr-1 opacity-70";
            titleSpan.appendChild(magnetIcon);
            titleSpan.appendChild(document.createTextNode(String(t.title || "")));
            a.appendChild(titleSpan);

            var statsWrapper = document.createElement("div");
            statsWrapper.className = "shrink-0 flex items-center gap-3";

            var peersDiv = document.createElement("div");
            peersDiv.className = "flex gap-2 text-[9px] font-mono";

            var seedersSpan = document.createElement("span");
            seedersSpan.className =
              "flex items-center gap-0.5 text-green-600/80 dark:text-green-400/80";
            var upIcon = document.createElement("i");
            upIcon.className = "icon ion-md-arrow-up";
            seedersSpan.appendChild(upIcon);
            seedersSpan.appendChild(document.createTextNode(String(t.seeders || "0")));

            var leechersSpan = document.createElement("span");
            leechersSpan.className =
              "flex items-center gap-0.5 text-red-600/80 dark:text-red-400/80";
            var downIcon = document.createElement("i");
            downIcon.className = "icon ion-md-arrow-down";
            leechersSpan.appendChild(downIcon);
            leechersSpan.appendChild(document.createTextNode(String(t.leechers || "0")));

            peersDiv.appendChild(seedersSpan);
            peersDiv.appendChild(leechersSpan);

            var sizeSpan = document.createElement("span");
            sizeSpan.className =
              "shrink-0 font-mono text-[10px] opacity-40 group-hover/torrent:opacity-100";
            sizeSpan.textContent = String(t.size || "");

            statsWrapper.appendChild(peersDiv);
            statsWrapper.appendChild(sizeSpan);
            a.appendChild(statsWrapper);

            grid.appendChild(a);
          });

          wrapper.appendChild(grid);
          contentPlaceholder.replaceChildren(wrapper);
        });
      })
      .catch(function (err) {
        console.error("Nyaa Load Error:", err);
        var empty = document.createElement("div");
        empty.className = "text-center py-12 opacity-40 italic";
        empty.textContent = I18n.t("Failed to load Nyaa resources");
        sidebarContainer.replaceChildren(empty);
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initBangumiIndex();
    initBangumiNyaa();
  });
})();
